"""Central source-capability policy for the catalogue pipeline.

The single authority for which file types the pipeline can actually process,
what their on-disk signatures look like, and where durable sources live by
default. Submission enforces it at the gate; the file-only raw stage
re-checks stored files against it.

This module is deliberately dependency-free (stdlib only) so raw-stage code
can import it without transitively pulling in the submission service,
contract runtime, extraction, interpretation or any AI provider.

Legacy ``.xls`` (OLE) is deliberately ABSENT from the capability set: the
configured extraction stage has no production ``.xls`` adapter (it returns
``UNSUPPORTED_LEGACY_XLS``), so accepting it at submission would queue runs
that are guaranteed to fail downstream.

Images are PRESENT on the same test read the other way. Extraction has read
them since 2026-07-23 — ``_extract_image`` sends the file to the vision
provider exactly as it sends a rendered PDF page — but this gate was written a
day later from the formats then in use and never listed them, so a supplier who
photographs their price list had to have it wrapped in a PDF by hand first.
Several do: Queen's Pharma sends theirs over WhatsApp, and AVM's VetriScience
list is a photograph too.
"""

from __future__ import annotations

DEFAULT_UPLOAD_ROOT = "/data/catalogue_uploads"

SUPPORTED_SOURCE_SUFFIXES = {
    ".pdf": "PDF",
    ".xlsx": "SPREADSHEET",
    ".csv": "CSV",
    ".jpg": "IMAGE",
    ".jpeg": "IMAGE",
    ".png": "IMAGE",
}


def signature_matches(source_format: str, header: bytes) -> bool:
    """File-signature check for the supported capability set.

    SPREADSHEET means modern XLSX (zip container) only — OLE signatures are
    rejected in line with the ``.xls`` capability decision above.

    IMAGE means JPEG or PNG, the two the extraction stage actually sends to a
    vision provider. A suffix alone is not enough: an unrecognised format falls
    through to False, so accepting a suffix here without its magic bytes would
    reject every upload of it as a signature mismatch.
    """

    if source_format in {"PDF", "PDF_TABLE"}:
        return header.startswith(b"%PDF")
    if source_format == "IMAGE":
        return header.startswith(b"\xff\xd8\xff") or header.startswith(b"\x89PNG\r\n\x1a\n")
    if source_format == "SPREADSHEET":
        return header.startswith(b"PK\x03\x04")
    if source_format == "CSV":
        return b"\x00" not in header
    return False


def format_satisfies_contract(recorded: str, contract_format: str) -> bool:
    """Whether a stored source's format can satisfy a contract's declared one.

    Asked in three places — at the submission gate, again at flow time against
    the recorded run, and once more when a reparse overrides the contract — so
    it lives here rather than being written out three times and drifting.

    A contract's ``source_format`` states the SHAPE of the content; the stored
    format states the carrier it arrived in. IMAGE satisfies the PDF contracts
    for that reason: a photograph of a price list is the same document as a
    scan of one, and the PDFs we hold for Queen's and for AVM's VetriScience
    list are those very images inside a wrapper. Keeping the contract on
    PDF_TABLE also keeps a page's header and footer classed as furniture
    instead of arriving BLOCKING, because conformance reads the CONTRACT's
    format and never the upload's.
    """
    if contract_format in {"PDF", "PDF_TABLE"}:
        return recorded in {"PDF", "IMAGE"}
    return recorded == contract_format


__all__ = [
    "DEFAULT_UPLOAD_ROOT",
    "SUPPORTED_SOURCE_SUFFIXES",
    "format_satisfies_contract",
    "signature_matches",
]
