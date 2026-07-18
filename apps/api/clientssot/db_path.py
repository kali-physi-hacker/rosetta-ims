# -*- coding: utf-8 -*-
"""Single source of truth for the SQLite DB path, shared by the Client SSOT router and all
ingest/dump/load scripts. Honours the same DATABASE_URL the main app uses (database.py), so on the
production droplet everything reads/writes the volume DB (/data/ims.db) — not a stray local file."""
import os
from pathlib import Path

_LOCAL = Path(__file__).resolve().parents[1] / "ims.db"   # backend/ims.db (dev default)

def resolve_db() -> Path:
    url = os.environ.get("DATABASE_URL", "")
    if url.startswith("sqlite:///"):
        p = url[len("sqlite:///"):]            # "/data/ims.db" (abs) or "./ims.db" (rel)
        if p.startswith("/") or (len(p) > 1 and p[1] == ":"):   # POSIX-abs or Windows drive
            return Path(p)
        return Path(__file__).resolve().parents[1] / p.lstrip("./") or _LOCAL
    return _LOCAL
