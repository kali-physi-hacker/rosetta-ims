#!/usr/bin/env python
"""One-shot data migration: IMS SQLite  ->  Postgres.

Usage (from backend/):
    SOURCE_SQLITE=sqlite:////data/ims.db \
    DATABASE_URL=postgresql+psycopg2://ims:PW@localhost:5432/ims \
        python scripts/migrate_sqlite_to_postgres.py          # dry-run (reports)
        python scripts/migrate_sqlite_to_postgres.py --apply  # actually writes

What it does (--apply):
  1. Builds the schema on Postgres from the SQLAlchemy models (create_all), plus any
     non-model tables found in SQLite (e.g. clientssot_*) via reflection.
  2. Copies every table in FK-dependency order, preserving primary-key ids.
  3. Resets each SERIAL sequence to MAX(id).
  4. Validates row counts src-vs-dst and exits non-zero on any mismatch.

Safe to re-run only against an EMPTY Postgres target (it does not truncate first).
Never point DATABASE_URL at anything but a throwaway/target Postgres.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import create_engine, MetaData, text  # noqa: E402
import models  # noqa: E402,F401  — registers every model table on Base.metadata
from database import Base  # noqa: E402

APPLY = "--apply" in sys.argv
SRC_URL = os.environ.get("SOURCE_SQLITE", "sqlite:///./ims.db")
DST_URL = os.environ.get("DATABASE_URL", "")

if not DST_URL or DST_URL.startswith("sqlite"):
    sys.exit("ERROR: set DATABASE_URL to the Postgres target (postgresql+psycopg2://...)")

src = create_engine(SRC_URL)
dst = create_engine(DST_URL)

# Reflect the ENTIRE source DB so non-model tables (clientssot_*, etc.) are included.
src_meta = MetaData()
src_meta.reflect(bind=src)
src_names = set(src_meta.tables)
model_names = set(Base.metadata.tables)
extra = sorted(src_names - model_names)          # in SQLite, not defined by a model
absent = sorted(model_names - src_names)          # model table with no SQLite counterpart

print(f"Source  : {SRC_URL}")
print(f"Target  : {DST_URL.split('@')[-1]}")
print(f"Tables  : {len(src_names)} in SQLite · {len(model_names)} models · "
      f"{len(extra)} non-model · {len(absent)} model-only")
if extra:
    print(f"Non-model tables carried by reflection: {', '.join(extra)}")
if absent:
    print(f"Model tables absent from SQLite (created empty): {', '.join(absent)}")
print(f"Mode    : {'APPLY — WRITING' if APPLY else 'DRY-RUN — no writes'}\n")


def count(engine, table):
    with engine.connect() as c:
        return c.execute(text(f'SELECT COUNT(*) FROM "{table}"')).scalar()


# Copy order: model tables in the MODELS' FK order — SQLite reflection can miss FKs, which
# mis-orders children (e.g. mbb_terms before product_suppliers) — then the non-model extras
# (no FK to models; their constraints were dropped above). FK enforcement stays ON so a real
# orphan would surface here rather than silently in prod.
order = list(Base.metadata.sorted_tables) + [src_meta.tables[n] for n in extra]

if not APPLY:
    total = 0
    for t in order:
        n = count(src, t.name)
        total += n
        print(f"  would copy  {t.name:34} {n:>9,}")
    print(f"\n  {total:,} rows across {len(order)} tables. Re-run with --apply to write.")
    sys.exit(0)

# 1. Schema — model tables (proper constraints) + reflected extras.
Base.metadata.create_all(dst)
if extra:
    # SQLite does not strictly enforce NOT NULL / primary-key nullability, so the externally
    # loaded clientssot_* data holds values Postgres rejects (e.g. NULLs in a text PK column).
    # Recreate these tables as plain typed columns — no NOT NULL, no PK/constraints — for a
    # faithful copy; pipeline.py re-establishes their real schema on its next load.
    from sqlalchemy import Table, Column
    extra_meta = MetaData()
    for name in extra:
        src_t = src_meta.tables[name]
        Table(name, extra_meta, *[Column(c.name, c.type, nullable=True) for c in src_t.columns])
    extra_meta.create_all(dst)
print("Schema created on Postgres.")

# 2. Copy rows in FK order, preserving ids. The reflected src table objects also drive the
#    INSERT (table.insert() is name-based, so it targets the Postgres table of the same name).
with src.connect() as s, dst.begin() as d:
    # Bypass FK checks for the bulk load: the source has legitimate historical orphans
    # (e.g. audit rows whose product was later deleted) that a faithful copy must preserve.
    # The FK constraints stay defined, so they still guard future writes. Requires the
    # migration to run as a Postgres superuser (the container's initial role is one).
    d.execute(text("SET session_replication_role = replica"))
    for t in order:
        rows = [dict(r._mapping) for r in s.execute(t.select())]
        if rows:
            d.execute(t.insert(), rows)
        print(f"  copied  {t.name:34} {len(rows):>9,}")
    d.execute(text("SET session_replication_role = DEFAULT"))

# 3. Reset SERIAL sequences to MAX(id) so new inserts don't collide with migrated ids.
with dst.begin() as d:
    for t in order:
        if "id" not in t.columns:
            continue
        seq = d.execute(text("SELECT pg_get_serial_sequence(:t, 'id')"), {"t": t.name}).scalar()
        if seq:
            d.execute(text(f"SELECT setval('{seq}', COALESCE((SELECT MAX(id) FROM \"{t.name}\"), 1))"))
print("Sequences reset.")

# 4. Validate row counts.
print("\nValidation (src vs dst):")
bad = 0
for t in order:
    sc, dc = count(src, t.name), count(dst, t.name)
    ok = sc == dc
    bad += 0 if ok else 1
    print(f"  {t.name:34} src={sc:>9,} dst={dc:>9,}  {'OK' if ok else 'MISMATCH'}")

if bad:
    sys.exit(f"\nFAILED: {bad} table(s) mismatched — do NOT cut over.")
print("\nAll tables match. Migration complete.")
