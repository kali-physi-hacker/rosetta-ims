"""Re-project every golden set's expected.csv FROM the Google Sheet.

The golden-sample tab of the Google Sheet is the source of truth; the repo's
expected.csv files are projections of it and must never be hand-edited to make
a test pass. When the sheet changes, run this; if the suite then goes red, the
red IS the truth disagreeing with the pipeline — fix sheet-side or
pipeline-side, never projection-side.

    python scripts/refresh_golden_expected.py                # pull the live sheet
    python scripts/refresh_golden_expected.py --file tab.csv # offline copy
    python scripts/refresh_golden_expected.py --set vetapet_vet

Projection rules, all declared here and in each set's expectations.json:
  * rows are selected by the set's `sheet_supplier_label` (the sheet's own
    supplier column value);
  * SKUs listed in the set's `parked_skus` are dropped — a parked row stays
    true in the sheet while a named mechanism gap keeps it out of the
    comparison (the expectations _parked_comment says why);
  * literal N/A markers (N/A, #N/A, NA, -) become blanks — the harness reads a
    non-blank cell as an assertion to enforce, and the export's own _clean()
    treats these markers as blanks already.
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import os
import sys
import tempfile
import urllib.request
from pathlib import Path

os.environ.setdefault("DATABASE_URL", f"sqlite:///{tempfile.mkdtemp()}/refresh.db")

from services.catalogue_golden_export import GOLDEN_COLUMNS  # noqa: E402

GOLDEN_ROOT = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "catalogue_pipeline" / "golden"
SHEET_URL = (
    "https://docs.google.com/spreadsheets/d/1Rly4jB0HpBtL6tCNwzXsMSqNfoL_cV3cSp4vDL8hkWI"
    "/export?format=csv&gid=1535624888"
)
_NA_MARKERS = {"N/A", "#N/A", "NA", "-"}


def _load_tab(source: str | None) -> list[dict[str, str]]:
    if source:
        text = Path(source).read_text(encoding="utf-8")
    else:
        with urllib.request.urlopen(SHEET_URL, timeout=60) as response:
            text = response.read().decode("utf-8")
    reader = csv.DictReader(io.StringIO(text))
    header = tuple(reader.fieldnames or ())
    if header != GOLDEN_COLUMNS:
        raise SystemExit(
            "the sheet tab's header drifted from GOLDEN_COLUMNS — the projection would be "
            f"meaningless.\n  sheet:  {list(header)}\n  export: {list(GOLDEN_COLUMNS)}"
        )
    return [row for row in reader if (row.get("supplier_product_code") or "").strip()]


def _project(cell: str | None) -> str:
    text = (cell or "").strip()
    return "" if text.upper() in _NA_MARKERS else text


def refresh(source: str | None, only: str | None) -> int:
    tab = _load_tab(source)
    failures = 0
    for set_dir in sorted(p for p in GOLDEN_ROOT.iterdir() if p.is_dir()):
        if only and set_dir.name != only:
            continue
        spec = json.loads((set_dir / "expectations.json").read_text(encoding="utf-8"))
        label = spec.get("sheet_supplier_label")
        if not label:
            print(f"{set_dir.name}: no sheet_supplier_label in expectations.json — skipped")
            continue
        parked = set(spec.get("parked_skus") or ())
        rows = [row for row in tab if (row.get("supplier") or "").strip() == label]
        kept = [row for row in rows if row["supplier_product_code"].strip() not in parked]
        if not kept:
            print(f"{set_dir.name}: ZERO sheet rows for supplier label {label!r} — refusing to write")
            failures += 1
            continue
        seen: set[str] = set()
        with (set_dir / "expected.csv").open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(GOLDEN_COLUMNS), lineterminator="\n")
            writer.writeheader()
            for row in kept:
                projected = {column: _project(row.get(column)) for column in GOLDEN_COLUMNS}
                code = projected["supplier_product_code"]
                # A row whose code projects to nothing ('N/A' placeholders)
                # asserts nothing addressable; a repeated code would make
                # last-row-wins comparisons silently ambiguous. Drop the junk,
                # keep the first occurrence, and say so.
                if not code:
                    print(f"{set_dir.name}: dropped a row with no usable product code")
                    continue
                if code in seen:
                    print(f"{set_dir.name}: dropped duplicate sheet row for {code!r} (first occurrence kept)")
                    continue
                seen.add(code)
                writer.writerow(projected)
        dropped = sorted(set(r["supplier_product_code"].strip() for r in rows) & parked)
        print(
            f"{set_dir.name}: {len(kept)} rows from label {label!r}"
            + (f" (parked, kept sheet-side: {dropped})" if dropped else "")
        )
    return failures


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--file", help="offline CSV copy of the tab instead of fetching the live sheet")
    parser.add_argument("--set", dest="only", help="refresh a single golden set by directory name")
    args = parser.parse_args()
    return refresh(args.file, args.only)


if __name__ == "__main__":
    sys.exit(main())
