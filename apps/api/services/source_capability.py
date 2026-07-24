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
"""

from __future__ import annotations

DEFAULT_UPLOAD_ROOT = "/data/catalogue_uploads"

SUPPORTED_SOURCE_SUFFIXES = {
    ".pdf": "PDF",
    ".xlsx": "SPREADSHEET",
    ".csv": "CSV",
}


def signature_matches(source_format: str, header: bytes) -> bool:
    """File-signature check for the supported capability set.

    SPREADSHEET means modern XLSX (zip container) only — OLE signatures are
    rejected in line with the ``.xls`` capability decision above.
    """

    if source_format in {"PDF", "PDF_TABLE"}:
        return header.startswith(b"%PDF")
    if source_format == "SPREADSHEET":
        return header.startswith(b"PK\x03\x04")
    if source_format == "CSV":
        return b"\x00" not in header
    return False


__all__ = ["DEFAULT_UPLOAD_ROOT", "SUPPORTED_SOURCE_SUFFIXES", "signature_matches"]
