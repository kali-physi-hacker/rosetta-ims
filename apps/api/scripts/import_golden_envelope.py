"""Import a hand-captured vision envelope as a golden fixture page.

For when a page is read by pasting the production prompt and the PDF into a
chat window rather than paying for an API call. Same model, same prompt — but
NOT the same call path, so the result is checked here and labelled honestly
rather than dropped into the fixtures as though it were a recording.

    python scripts/import_golden_envelope.py <outdir> <page-number> <pasted.json>
    python scripts/import_golden_envelope.py <outdir> 18 -        # read stdin

What this refuses:
  * anything _VisionEnvelope rejects — the same gate the live path passes
    through, so a fixture can never be looser than production evidence
  * a row carrying more cells than its table declares columns, which is the
    usual symptom of a chat response drifting out of alignment
  * overwriting a page that already exists

Provenance goes into meta.json as "hand-captured", never "live". A golden set
that misstates how it was read cannot be argued with later, and the whole point
of these fixtures is that they are the arbiter.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from services.catalogue_evidence_extraction import _strict_json_object, _VisionEnvelope


def _describe(envelope: _VisionEnvelope) -> str:
    tables = len(envelope.tables)
    rows = sum(len(t.rows) for t in envelope.tables) + len(envelope.rows)
    texts = len(envelope.text_observations)
    return f"{envelope.page_outcome} — {tables} table(s), {rows} row(s), {texts} text observation(s)"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("outdir", type=Path, help="fixture directory, e.g. tests/fixtures/catalogue_pipeline/golden/alfamedic")
    parser.add_argument("page", type=int, help="1-based page number in the source PDF")
    parser.add_argument("json_file", help="file containing the pasted JSON, or - for stdin")
    parser.add_argument("--source", default="", help="source PDF filename, recorded in meta.json on first import")
    parser.add_argument("--model", default="claude (chat)", help="which model produced it, recorded in meta.json")
    args = parser.parse_args()

    raw = sys.stdin.read() if args.json_file == "-" else Path(args.json_file).read_text(encoding="utf-8")

    # Same two gates as the live path: tolerate fenced/ragged JSON, then hold
    # the parsed object to the authoritative envelope contract.
    payload = _strict_json_object(raw)
    envelope = _VisionEnvelope.model_validate(payload)

    args.outdir.mkdir(parents=True, exist_ok=True)
    out = args.outdir / f"page_{args.page}.json"
    if out.exists():
        raise SystemExit(f"{out} already exists — delete it first if you mean to replace it")
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"page {args.page}: {_describe(envelope)} -> {out}")

    meta_path = args.outdir / "meta.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.exists() else {}
    meta.setdefault("source", args.source or "unknown")
    # Deliberately not "live ... extraction": these were read in a chat window,
    # so they exercise the parse path but say nothing about the production call.
    meta["recorded_from"] = f"hand-captured from {args.model} via scripts/import_golden_envelope.py"
    meta["provider"] = "hand-captured"
    meta["model"] = args.model
    meta["pages"] = sorted({*meta.get("pages", []), args.page})
    meta_path.write_text(json.dumps(meta, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
