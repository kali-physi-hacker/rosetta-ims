"""CLI over services.upload_workbook.

    python scripts/build_upload_workbook.py --template rosetta-upload.xlsx
    python scripts/build_upload_workbook.py --export   rosetta-products.xlsx

`--template` writes an empty workbook to add products with. `--export` fills
it with what is already recorded, which is how you MODIFY: editing real rows
beats retyping SKUs.

Both need DATABASE_URL. Even the empty one is built from the database — the
supplier, category and unit dropdowns are read at build time, which is the
whole reason this is a script rather than a file someone drew once.

The inventory page's Export builds the same workbook through the same service,
filtered to whatever the page is showing. This exists for the times nobody
wants to open a browser — a full snapshot, a scripted rebuild after adding
suppliers, a look at what the dropdowns currently offer.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

# This used to default to a throwaway sqlite file on the grounds that a
# template carries no data. It carries no ROWS; it still reads suppliers,
# categories and units to build the dropdowns, so the throwaway database only
# ever produced "no such table: suppliers" at the first query. Say what is
# missing instead.
if not os.environ.get("DATABASE_URL"):
    raise SystemExit(
        "DATABASE_URL is not set, and the dropdowns are read from the database.\n"
        "  DATABASE_URL=sqlite:///data/ims.db python scripts/build_upload_workbook.py --template out.xlsx"
    )

from services.upload_workbook import build  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--template", metavar="PATH", help="empty workbook, for adding")
    group.add_argument("--export", metavar="PATH", help="filled with current data, for editing")
    parser.add_argument("--limit", type=int, default=None, help="cap exported products")
    args = parser.parse_args()

    path = args.template or args.export
    result = build(path, with_data=bool(args.export), limit=args.limit)
    print(
        f"wrote {result['path']}  "
        f"{result['skus']} SKUs in {result['rows']} rows, {result['deals']} deals  "
        f"dropdowns: {result['suppliers']} suppliers, {result['brands']} brands, {result['categories']} categories, "
        f"{result['uoms']} units"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
