"""Record REAL Gemini vision envelopes for golden supplier fixtures.

Runs the production extraction call (same model/prompt/config as the pipeline)
page-by-page against a real supplier PDF and writes each page's raw provider
envelope to a fixture directory:

    python scripts/record_golden_envelopes.py <pdf> <outdir> [--pages 1,4,5]

Each page is recorded SEQUENTIALLY (deterministic, resumable — pages already
present in <outdir> are skipped). The recorded files are exactly what
``_call_gemini_vision`` returned, so tests replaying them exercise the same
parse/validate path as production. Requires GEMINI_API_KEY.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pypdf

from services.catalogue_evidence_extraction import (
    GEMINI_MODEL,
    _call_gemini_vision,
    _single_page_pdf_bytes,
    _strict_json_object,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pdf", type=Path)
    parser.add_argument("outdir", type=Path)
    parser.add_argument("--pages", help="1-based page numbers, comma-separated. Default: all pages.")
    args = parser.parse_args()

    reader = pypdf.PdfReader(str(args.pdf))
    if reader.is_encrypted and not reader.decrypt(""):
        raise SystemExit("PDF requires a real user password; cannot record")
    wanted = (
        sorted({int(part) for part in args.pages.split(",")})
        if args.pages
        else list(range(1, len(reader.pages) + 1))
    )
    args.outdir.mkdir(parents=True, exist_ok=True)

    recorded = skipped = 0
    for page_number in wanted:
        out = args.outdir / f"page_{page_number}.json"
        if out.exists():
            skipped += 1
            continue
        page_bytes = _single_page_pdf_bytes(reader.pages[page_number - 1])
        response = _call_gemini_vision(page_bytes, media_type="application/pdf")
        envelope = _strict_json_object(response.text)  # fail fast on malformed output
        out.write_text(json.dumps(envelope, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        observations = len(envelope.get("observations", []))
        print(f"page {page_number}: {envelope.get('page_outcome')} — {observations} observations -> {out}")
        recorded += 1

    meta = args.outdir / "meta.json"
    if not meta.exists():
        meta.write_text(
            json.dumps(
                {
                    "source": args.pdf.name,
                    "recorded_from": f"live {GEMINI_MODEL} extraction via scripts/record_golden_envelopes.py",
                    "pages": wanted,
                },
                indent=1,
            )
            + "\n",
            encoding="utf-8",
        )
    print(f"recorded={recorded} skipped(existing)={skipped}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
