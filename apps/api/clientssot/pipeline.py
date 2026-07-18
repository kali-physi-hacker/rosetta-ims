# -*- coding: utf-8 -*-
"""Client SSOT data pipeline — dump / load (production hand-off, per Desmond).

Decouples extraction (dump) from loading (load) so the same snapshot can target prod.

  Dump local Client SSOT data to a portable compressed snapshot:
      python -m clientssot.pipeline dump                      # -> clientssot_data.db.gz
  Load that snapshot into the production DB (auto-targets DATABASE_URL = /data/ims.db on the droplet):
      python -m clientssot.pipeline load                      # uses clientssot_data.db.gz
      python -m clientssot.pipeline load --in <file> --db /path/to/ims.db   # explicit override

Schema-agnostic: copies EVERY clientssot_* table with its EXACT schema + indexes straight from
sqlite_master, so it never goes stale as columns change. SAFE: only ever drops/creates tables whose
name starts with 'clientssot' — the inventory/users/suppliers tables are never touched."""
import argparse, gzip, shutil, sqlite3, sys, io, tempfile, os
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from clientssot.db_path import resolve_db
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

DEFAULT_SNAPSHOT = Path(__file__).resolve().parent / "clientssot_data.db.gz"   # co-located with the code

def _objects(con, schema):
    """(tables, indexes) as [(name, ddl)] for every clientssot_* object in the given schema."""
    rows = con.execute(
        f"SELECT name, type, sql, tbl_name FROM {schema}.sqlite_master "
        f"WHERE sql IS NOT NULL AND (name LIKE 'clientssot%' OR tbl_name LIKE 'clientssot%') "
        f"ORDER BY name").fetchall()
    tables = [(n, sql) for (n, t, sql, tbl) in rows if t == "table"]
    indexes = [(n, sql) for (n, t, sql, tbl) in rows if t == "index"]
    return tables, indexes

def dump(out):
    src = resolve_db()
    if not Path(src).exists():
        print("ERROR: source DB not found:", src); sys.exit(1)
    scon = sqlite3.connect(src)
    tables, indexes = _objects(scon, "main")
    scon.close()
    if not tables:
        print("ERROR: no clientssot_* tables in", src); sys.exit(1)
    tmp = Path(tempfile.gettempdir()) / "clientssot_snapshot.db"
    if tmp.exists():
        tmp.unlink()
    dcon = sqlite3.connect(tmp)
    dcon.execute("ATTACH DATABASE ? AS src", (str(src),))
    counts = {}
    for name, ddl in tables:
        dcon.execute(ddl)                                              # original DDL -> snapshot.main
        dcon.execute(f'INSERT INTO main."{name}" SELECT * FROM src."{name}"')
        counts[name] = dcon.execute(f'SELECT COUNT(*) FROM main."{name}"').fetchone()[0]
    for _, ddl in indexes:
        try:
            dcon.execute(ddl)
        except sqlite3.OperationalError:
            pass
    dcon.commit(); dcon.execute("DETACH src"); dcon.close()
    with open(tmp, "rb") as fi, gzip.open(out, "wb", compresslevel=6) as fo:
        shutil.copyfileobj(fi, fo)
    size_mb = round(os.path.getsize(out) / 1e6, 1)
    tmp.unlink()
    print("DUMPED tables:", counts)
    print(f"-> {out}  ({size_mb} MB gzipped, {len(tables)} tables, {len(indexes)} indexes)")

def load(infile, target):
    if not Path(infile).exists():
        print("ERROR: snapshot not found:", infile); sys.exit(1)
    target = target or str(resolve_db())
    tmp = Path(tempfile.gettempdir()) / "clientssot_load.db"
    with gzip.open(infile, "rb") as fi, open(tmp, "wb") as fo:
        shutil.copyfileobj(fi, fo)
    con = sqlite3.connect(target)
    con.execute("ATTACH DATABASE ? AS snap", (str(tmp),))
    tables, indexes = _objects(con, "snap")
    counts = {}
    for name, ddl in tables:
        assert name.startswith("clientssot"), f"refusing to touch non-clientssot table {name}"
        con.execute(f'DROP TABLE IF EXISTS main."{name}"')
        con.execute(ddl)                                              # original DDL -> target.main
        con.execute(f'INSERT INTO main."{name}" SELECT * FROM snap."{name}"')
        counts[name] = con.execute(f'SELECT COUNT(*) FROM main."{name}"').fetchone()[0]
    for name, ddl in indexes:
        try:
            con.execute(f'DROP INDEX IF EXISTS main."{name}"')
            con.execute(ddl)
        except sqlite3.OperationalError:
            pass
    con.commit(); con.execute("DETACH snap"); con.close(); tmp.unlink()
    print("LOADED into", target)
    print("tables:", counts)
    print(f"({len(tables)} tables, {len(indexes)} indexes) — inventory/users tables untouched.")

if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Client SSOT dump/load pipeline")
    sub = ap.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("dump"); d.add_argument("--out", default=str(DEFAULT_SNAPSHOT))
    l = sub.add_parser("load"); l.add_argument("--in", dest="infile", default=str(DEFAULT_SNAPSHOT)); l.add_argument("--db", default=None)
    a = ap.parse_args()
    if a.cmd == "dump":
        dump(a.out)
    elif a.cmd == "load":
        load(a.infile, a.db)
