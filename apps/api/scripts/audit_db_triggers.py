"""Out-of-band write backstop — SQLite triggers that catch DIRECT-to-database row changes.

The application audit (audit_log) captures every action taken THROUGH the API. It cannot see a
row written straight to the database by a script or a psql/sqlite session — which is exactly how
the untraceable competitor batch got in. These triggers close that gap: every INSERT/DELETE on a
sensitive table is stamped into a dedicated `db_change_log` table at the database level, so it
fires no matter who does the write.

Design choices:
  * A SEPARATE table (not audit_log) so the rich, attributed app trail stays clean — you only read
    db_change_log to HUNT for changes that have no matching app action (i.e. out-of-band writes).
  * INSERT + DELETE only (not UPDATE): the motivating gap was rows appearing/disappearing without a
    trace; per-row UPDATE churn (e.g. the competitor-price scraper) would be pure noise and is
    already covered by the app's before/after audit.
  * Idempotent: DROP-IF-EXISTS then CREATE, so it is safe to re-run after a schema change.

Usage (run on the server, like any migration):
    python scripts/audit_db_triggers.py            # DRY RUN — prints the DDL, writes nothing
    python scripts/audit_db_triggers.py --apply    # install the table + triggers
    python scripts/audit_db_triggers.py --verify    # show trigger + change-log status
"""
import os
import sys
import sqlite3

# Sensitive / low-churn tables where an out-of-band row change matters most. (table, pk_col, label_col)
TABLES = [
    ("competitor_prices", "id",       "competitor_name"),
    ("category_rules",    "category", "category"),        # GP floors
    ("product_channels",  "id",       "channel"),         # selling prices
    ("mbb_terms",         "id",       "id"),              # bulk-buy cost tiers
    ("suppliers",         "id",       "name"),
    ("users",             "id",       "username"),        # access
]

CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS db_change_log (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  at         TEXT NOT NULL,
  table_name TEXT NOT NULL,
  op         TEXT NOT NULL,
  row_pk     TEXT,
  label      TEXT
);
""".strip()


def _trigger_sql(table: str, pk: str, label: str, op: str) -> tuple[str, str]:
    """Return (drop_stmt, create_stmt) for one AFTER-<op> trigger."""
    name = f"dcl_{table}_{op[:3]}"
    ref = "NEW" if op == "insert" else "OLD"
    drop = f"DROP TRIGGER IF EXISTS {name};"
    create = (
        f"CREATE TRIGGER {name} AFTER {op.upper()} ON {table}\n"
        f"BEGIN\n"
        f"  INSERT INTO db_change_log(at, table_name, op, row_pk, label)\n"
        f"  VALUES (strftime('%Y-%m-%dT%H:%M:%f','now'), '{table}', '{op}', "
        f"{ref}.{pk}, {ref}.{label});\n"
        f"END;"
    )
    return drop, create


def all_statements() -> list[str]:
    stmts = [CREATE_TABLE]
    for table, pk, label in TABLES:
        for op in ("insert", "delete"):
            drop, create = _trigger_sql(table, pk, label, op)
            stmts += [drop, create]
    return stmts


def db_path() -> str:
    url = os.environ.get("DATABASE_URL", "sqlite:////data/ims.db")
    return url.split("sqlite:///")[-1] if url.startswith("sqlite") else "/data/ims.db"


def main(argv):
    apply = "--apply" in argv
    verify = "--verify" in argv
    path = db_path()
    con = sqlite3.connect(path)
    cur = con.cursor()

    if verify:
        trigs = [r[0] for r in cur.execute(
            "SELECT name FROM sqlite_master WHERE type='trigger' AND name LIKE 'dcl_%' ORDER BY name")]
        has_tbl = cur.execute(
            "SELECT count(*) FROM sqlite_master WHERE type='table' AND name='db_change_log'").fetchone()[0]
        n = cur.execute("SELECT count(*) FROM db_change_log").fetchone()[0] if has_tbl else 0
        print(f"db: {path}")
        print(f"db_change_log table: {'present' if has_tbl else 'MISSING'} ({n} rows)")
        print(f"installed triggers ({len(trigs)}): {', '.join(trigs) or 'none'}")
        con.close()
        return

    stmts = all_statements()
    if not apply:
        print(f"DRY RUN — would run {len(stmts)} statements against {path}:\n")
        print("\n".join(stmts))
        print("\nRe-run with --apply to install.")
        con.close()
        return

    for s in stmts:
        cur.execute(s)
    con.commit()
    trigs = cur.execute(
        "SELECT count(*) FROM sqlite_master WHERE type='trigger' AND name LIKE 'dcl_%'").fetchone()[0]
    print(f"APPLIED to {path}: db_change_log ready, {trigs} backstop triggers installed "
          f"across {len(TABLES)} tables.")
    con.close()


if __name__ == "__main__":
    main(sys.argv[1:])
