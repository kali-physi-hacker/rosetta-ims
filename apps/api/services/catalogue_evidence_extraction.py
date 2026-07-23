"""Typed, source-located extraction for the boundary immediately before Raw.

This module deliberately does not parse catalogue evidence into product fields.
It records what was observed and where it was observed. Semantic interpretation
belongs after Raw persistence.
"""

from __future__ import annotations

import base64
import csv
import io
import json
import os
import platform
import re
from decimal import Decimal
from enum import Enum
from pathlib import Path
from typing import Any

import openpyxl
import pypdf
from openpyxl.utils import get_column_letter
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from schemas.catalogue_pipeline.enums import ExtractionMethod, SourceFormat
from schemas.catalogue_pipeline.raw_observation_v1 import BoundingBox, RawCell, SourceLocation


ANTHROPIC_MODEL = "claude-haiku-4-5-20251001"
MAX_VISION_TOKENS = 8192


class ExtractionStatus(str, Enum):
    """Completeness of one source extraction attempt."""

    COMPLETE = "COMPLETE"
    PARTIAL = "PARTIAL"
    FAILED = "FAILED"


class ExtractionError(BaseModel):
    """Sanitized operational error attached to an extraction result."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    code: str = Field(..., min_length=1)
    message: str = Field(..., min_length=1)
    unit_key: str | None = None
    provider: str | None = None
    retryable: bool = False


class ExtractedEvidence(BaseModel):
    """One verbatim, source-located observation ready for Raw persistence."""

    model_config = ConfigDict(extra="forbid", frozen=True, protected_namespaces=())

    observation_key: str = Field(..., min_length=1)
    source_location: SourceLocation
    raw_text: str | None = None
    raw_cells: tuple[RawCell, ...] = ()
    extraction_method: ExtractionMethod
    provider: str | None = None
    provider_version: str | None = None
    provider_request_id: str | None = None
    model: str | None = None
    model_version: str | None = None
    confidence: Decimal | None = Field(None, ge=Decimal("0"), le=Decimal("1"))
    warnings: tuple[str, ...] = ()
    source_metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("confidence", mode="before")
    @classmethod
    def _confidence_not_float(cls, value):
        if isinstance(value, float):
            raise ValueError("confidence must be a decimal string, integer, or Decimal")
        return value

    @model_validator(mode="after")
    def _requires_verbatim_evidence(self):
        has_text = bool(self.raw_text and self.raw_text.strip())
        has_cells = any(cell.raw_value is not None and str(cell.raw_value).strip() for cell in self.raw_cells)
        if not has_text and not has_cells:
            raise ValueError("ExtractedEvidence requires raw_text or at least one non-empty raw cell")
        return self


class ExtractionResult(BaseModel):
    """Typed extraction envelope with explicit completeness accounting."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    status: ExtractionStatus
    source_format: SourceFormat
    observations: tuple[ExtractedEvidence, ...] = ()
    units_attempted: int = Field(..., ge=0)
    units_completed: int = Field(..., ge=0)
    warnings: tuple[str, ...] = ()
    errors: tuple[ExtractionError, ...] = ()

    @model_validator(mode="after")
    def _status_matches_contents(self):
        if self.units_completed > self.units_attempted:
            raise ValueError("units_completed cannot exceed units_attempted")
        if self.status == ExtractionStatus.COMPLETE:
            if not self.observations:
                raise ValueError("COMPLETE extraction requires at least one observation")
            if self.errors or self.units_completed != self.units_attempted:
                raise ValueError("COMPLETE extraction cannot contain errors or incomplete units")
        elif self.status == ExtractionStatus.PARTIAL:
            if not self.observations or not self.errors:
                raise ValueError("PARTIAL extraction requires observations and errors")
        elif self.status == ExtractionStatus.FAILED:
            if self.observations or not self.errors:
                raise ValueError("FAILED extraction requires errors and cannot contain observations")
        return self


class _VisionObservation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    raw_text: str | None = None
    raw_cells: tuple[RawCell, ...] = ()
    bounding_box: BoundingBox | None = None
    confidence: Decimal | None = Field(None, ge=Decimal("0"), le=Decimal("1"))

    @field_validator("raw_cells")
    @classmethod
    def _cell_values_are_verbatim_strings(cls, value):
        for cell in value:
            if not isinstance(cell.raw_value, str):
                raise ValueError("vision raw cell values must be verbatim strings")
        return value

    @model_validator(mode="after")
    def _requires_evidence(self):
        has_text = bool(self.raw_text and self.raw_text.strip())
        has_cells = any(cell.raw_value is not None and str(cell.raw_value).strip() for cell in self.raw_cells)
        if not has_text and not has_cells:
            raise ValueError("vision observation requires verbatim text or cells")
        return self


class _VisionEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid")

    observations: tuple[_VisionObservation, ...]


class _VisionResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    text: str
    request_id: str | None = None


VISION_EVIDENCE_PROMPT = """Extract only verbatim catalogue evidence from this source.

Identify each visually distinct catalogue row or product line. Do not interpret,
translate, normalize, calculate, split variants, infer product fields, or remove
duplicates. Preserve repeated rows at their separate source locations.

Return one JSON object with exactly this shape:
{
  "observations": [
    {
      "raw_text": "the complete row exactly as printed, or null",
      "raw_cells": [
        {
          "cell_reference": null,
          "row_number": null,
          "column_name": "the printed column heading, or null",
          "column_index": 1,
          "raw_value": "the cell value exactly as printed"
        }
      ],
      "bounding_box": {
        "x": 0,
        "y": 0,
        "width": 1,
        "height": 1,
        "unit": "px"
      },
      "confidence": "0.95"
    }
  ]
}

Use strings for confidence values. Omit no visible catalogue row. Use null for
unknown optional values. Return only the JSON object, without Markdown fences.
"""


def extract_evidence(content: bytes, filename: str, content_type: str) -> ExtractionResult:
    """Extract verbatim observations without performing semantic product parsing."""

    source_kind, source_format = _classify_source(filename, content_type)
    if not content:
        return _failed_result(
            source_format,
            code="EMPTY_SOURCE",
            message="Source content is empty",
        )
    if source_kind == "xlsx":
        return _extract_spreadsheet(content)
    if source_kind == "xls":
        return _failed_result(
            source_format,
            code="UNSUPPORTED_LEGACY_XLS",
            message="Legacy .xls files are not supported by the configured spreadsheet reader",
        )
    if source_kind == "csv":
        return _extract_csv(content)
    if source_kind == "pdf":
        return _extract_pdf(content)
    if source_kind in {"jpeg", "png"}:
        media_type = "image/png" if source_kind == "png" else "image/jpeg"
        return _extract_image(content, media_type=media_type, source_format=source_format)
    if source_kind == "text":
        return _extract_text(content, source_format=source_format)
    return _failed_result(
        source_format,
        code="UNSUPPORTED_SOURCE_FORMAT",
        message=f"Unsupported catalogue source type for {Path(filename).name or 'upload'}",
    )


def _extract_spreadsheet(content: bytes) -> ExtractionResult:
    try:
        workbook = openpyxl.load_workbook(io.BytesIO(content), read_only=True, data_only=False)
    except Exception:
        return _failed_result(
            SourceFormat.SPREADSHEET,
            code="MALFORMED_SPREADSHEET",
            message="Spreadsheet source could not be read",
        )

    observations: list[ExtractedEvidence] = []
    warnings: list[str] = []
    errors: list[ExtractionError] = []
    completed = 0
    sheets = tuple(workbook.worksheets)
    try:
        for sheet in sheets:
            observed_rows = 0
            try:
                for row in sheet.iter_rows():
                    non_empty = [cell for cell in row if cell.value is not None and str(cell.value).strip()]
                    if not non_empty:
                        continue
                    observed_rows += 1
                    first_column = min(cell.column for cell in non_empty)
                    last_column = max(cell.column for cell in non_empty)
                    row_number = non_empty[0].row
                    cell_range = (
                        f"{get_column_letter(first_column)}{row_number}:"
                        f"{get_column_letter(last_column)}{row_number}"
                    )
                    key = f"sheet:{sheet.title}:row:{row_number}"
                    raw_cells = tuple(
                        RawCell(
                            cell_reference=cell.coordinate,
                            row_number=cell.row,
                            column_index=cell.column,
                            raw_value=cell.value,
                        )
                        for cell in row
                    )
                    observations.append(
                        ExtractedEvidence(
                            observation_key=key,
                            source_location=SourceLocation(
                                sheet_name=sheet.title,
                                row_number=row_number,
                                cell_range=cell_range,
                                source_object_key=key,
                            ),
                            raw_cells=raw_cells,
                            extraction_method=ExtractionMethod.SPREADSHEET_CELL,
                            provider="openpyxl",
                            provider_version=openpyxl.__version__,
                        )
                    )
            except Exception:
                errors.append(
                    ExtractionError(
                        code="SPREADSHEET_SHEET_READ_ERROR",
                        message="One spreadsheet sheet could not be read completely",
                        unit_key=f"sheet:{sheet.title}",
                        provider="openpyxl",
                    )
                )
                continue
            completed += 1
            if not observed_rows:
                warnings.append(f"sheet {sheet.title!r} contained no non-empty rows")
    finally:
        workbook.close()

    return _build_result(
        SourceFormat.SPREADSHEET,
        observations=observations,
        units_attempted=len(sheets),
        units_completed=completed,
        warnings=warnings,
        errors=errors,
    )


def _extract_csv(content: bytes) -> ExtractionResult:
    try:
        text, encoding = _decode_delimited_text(content)
    except UnicodeDecodeError:
        return _failed_result(
            SourceFormat.CSV,
            code="UNSUPPORTED_TEXT_ENCODING",
            message="CSV source is not valid UTF-8 or Big5 text",
        )
    try:
        dialect = _sniff_dialect(text)
        rows = list(csv.reader(io.StringIO(text), dialect))
    except csv.Error:
        return _failed_result(
            SourceFormat.CSV,
            code="MALFORMED_CSV",
            message="CSV source could not be parsed",
        )

    observations: list[ExtractedEvidence] = []
    for row_number, row in enumerate(rows, start=1):
        if not any(value.strip() for value in row):
            continue
        key = f"csv:row:{row_number}"
        last_column = max(1, len(row))
        observations.append(
            ExtractedEvidence(
                observation_key=key,
                source_location=SourceLocation(
                    row_number=row_number,
                    cell_range=f"A{row_number}:{get_column_letter(last_column)}{row_number}",
                    source_object_key=key,
                ),
                raw_cells=tuple(
                    RawCell(
                        cell_reference=f"{get_column_letter(column_index)}{row_number}",
                        row_number=row_number,
                        column_index=column_index,
                        raw_value=value,
                    )
                    for column_index, value in enumerate(row, start=1)
                ),
                extraction_method=ExtractionMethod.SPREADSHEET_CELL,
                provider="python-csv",
                provider_version=platform.python_version(),
                source_metadata={"encoding": encoding, "delimiter": dialect.delimiter},
            )
        )

    return _build_result(
        SourceFormat.CSV,
        observations=observations,
        units_attempted=len(rows),
        units_completed=len(rows),
    )


def _extract_text(content: bytes, *, source_format: SourceFormat) -> ExtractionResult:
    try:
        text, encoding = _decode_delimited_text(content)
    except UnicodeDecodeError:
        return _failed_result(
            source_format,
            code="UNSUPPORTED_TEXT_ENCODING",
            message="Text source is not valid UTF-8 or Big5 text",
        )
    lines = text.splitlines()
    observations = [
        ExtractedEvidence(
            observation_key=f"text:line:{line_number}",
            source_location=SourceLocation(
                source_object_key=f"text:line:{line_number}",
            ),
            raw_text=line,
            extraction_method=ExtractionMethod.OTHER,
            provider="python-text",
            provider_version=platform.python_version(),
            source_metadata={"encoding": encoding, "line_number": line_number},
        )
        for line_number, line in enumerate(lines, start=1)
        if line.strip()
    ]
    return _build_result(
        source_format,
        observations=observations,
        units_attempted=len(lines),
        units_completed=len(lines),
    )


def _extract_pdf(content: bytes) -> ExtractionResult:
    try:
        reader = pypdf.PdfReader(io.BytesIO(content))
    except Exception:
        return _failed_result(
            SourceFormat.PDF,
            code="MALFORMED_PDF",
            message="PDF source could not be read",
        )

    observations: list[ExtractedEvidence] = []
    warnings: list[str] = []
    errors: list[ExtractionError] = []
    completed = 0
    for page_number, page in enumerate(reader.pages, start=1):
        try:
            page_text = page.extract_text() or ""
        except Exception:
            page_text = ""
            warnings.append(f"page {page_number} text layer could not be decoded; vision fallback required")
        if _pdf_text_is_reliable(page_text):
            observations.extend(_pdf_text_observations(page_text, page_number=page_number))
            completed += 1
            continue
        page_key = f"page:{page_number}"
        if not _anthropic_api_key():
            errors.append(
                ExtractionError(
                    code="EXTRACTION_CONFIGURATION_ERROR",
                    message="Scanned or unreadable PDF page requires a configured vision provider",
                    unit_key=page_key,
                    provider="anthropic",
                )
            )
            continue
        try:
            page_content = _single_page_pdf_bytes(page)
            response = _call_anthropic_vision(
                page_content,
                media_type="application/pdf",
            )
            observations.extend(
                _vision_observations(
                    response,
                    extraction_method=ExtractionMethod.MODEL_VISION,
                    unit_key=page_key,
                    page_number=page_number,
                )
            )
            completed += 1
        except _VisionExtractionFailure as exc:
            errors.append(
                ExtractionError(
                    code=exc.code,
                    message=exc.public_message,
                    unit_key=page_key,
                    provider="anthropic",
                    retryable=exc.retryable,
                )
            )
        except Exception:
            errors.append(
                ExtractionError(
                    code="SOURCE_PAGE_READ_ERROR",
                    message="PDF page could not be prepared for vision extraction",
                    unit_key=page_key,
                    provider="pypdf",
                )
            )
    if not len(reader.pages):
        warnings.append("PDF contained no pages")
    return _build_result(
        SourceFormat.PDF,
        observations=observations,
        units_attempted=len(reader.pages),
        units_completed=completed,
        warnings=warnings,
        errors=errors,
    )


def _extract_image(
    content: bytes,
    *,
    media_type: str,
    source_format: SourceFormat,
) -> ExtractionResult:
    if not _anthropic_api_key():
        return _failed_result(
            source_format,
            code="EXTRACTION_CONFIGURATION_ERROR",
            message="Image evidence extraction requires a configured vision provider",
            unit_key="image:1",
            provider="anthropic",
        )
    try:
        response = _call_anthropic_vision(content, media_type=media_type)
        observations = _vision_observations(
            response,
            extraction_method=ExtractionMethod.MODEL_VISION,
            unit_key="image:1",
            page_number=None,
        )
    except _VisionExtractionFailure as exc:
        return _failed_result(
            source_format,
            code=exc.code,
            message=exc.public_message,
            unit_key="image:1",
            provider="anthropic",
            retryable=exc.retryable,
            units_attempted=1,
        )
    return _build_result(
        source_format,
        observations=observations,
        units_attempted=1,
        units_completed=1,
    )


def _pdf_text_observations(text: str, *, page_number: int) -> list[ExtractedEvidence]:
    observations: list[ExtractedEvidence] = []
    for line_number, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        key = f"page:{page_number}:line:{line_number}"
        observations.append(
            ExtractedEvidence(
                observation_key=key,
                source_location=SourceLocation(
                    page_number=page_number,
                    source_object_key=key,
                ),
                raw_text=line,
                extraction_method=ExtractionMethod.PDF_TEXT,
                provider="pypdf",
                provider_version=pypdf.__version__,
            )
        )
    return observations


def _vision_observations(
    response: _VisionResponse,
    *,
    extraction_method: ExtractionMethod,
    unit_key: str,
    page_number: int | None,
) -> list[ExtractedEvidence]:
    try:
        payload = _strict_json_object(response.text)
        envelope = _VisionEnvelope.model_validate(payload)
    except (json.JSONDecodeError, ValueError) as exc:
        raise _VisionExtractionFailure(
            code="MALFORMED_PROVIDER_RESPONSE",
            public_message="Vision provider returned an invalid evidence envelope",
            retryable=False,
        ) from exc

    try:
        observations: list[ExtractedEvidence] = []
        for index, item in enumerate(envelope.observations, start=1):
            key = f"{unit_key}:observation:{index}"
            observations.append(
                ExtractedEvidence(
                    observation_key=key,
                    source_location=SourceLocation(
                        page_number=page_number,
                        bounding_box=item.bounding_box,
                        source_object_key=key,
                    ),
                    raw_text=item.raw_text,
                    raw_cells=item.raw_cells,
                    extraction_method=extraction_method,
                    provider="anthropic",
                    provider_request_id=response.request_id,
                    model=ANTHROPIC_MODEL,
                    model_version=ANTHROPIC_MODEL,
                    confidence=item.confidence,
                )
            )
    except ValueError as exc:
        raise _VisionExtractionFailure(
            code="MALFORMED_PROVIDER_RESPONSE",
            public_message="Vision provider returned invalid source evidence",
            retryable=False,
        ) from exc
    return observations


def _call_anthropic_vision(content: bytes, *, media_type: str) -> _VisionResponse:
    try:
        import anthropic

        client = anthropic.Anthropic(api_key=_anthropic_api_key())
        block_type = "document" if media_type == "application/pdf" else "image"
        message = client.messages.create(
            model=ANTHROPIC_MODEL,
            max_tokens=MAX_VISION_TOKENS,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": block_type,
                            "source": {
                                "type": "base64",
                                "media_type": media_type,
                                "data": base64.standard_b64encode(content).decode(),
                            },
                        },
                        {"type": "text", "text": VISION_EVIDENCE_PROMPT},
                    ],
                }
            ],
        )
        text_blocks = [block.text for block in message.content if getattr(block, "type", None) == "text"]
        if not text_blocks:
            raise _VisionExtractionFailure(
                code="MALFORMED_PROVIDER_RESPONSE",
                public_message="Vision provider returned no text response",
                retryable=False,
            )
        return _VisionResponse(
            text="\n".join(text_blocks),
            request_id=getattr(message, "id", None),
        )
    except _VisionExtractionFailure:
        raise
    except Exception as exc:
        retryable = _looks_transient(exc)
        raise _VisionExtractionFailure(
            code="TRANSIENT_PROVIDER_ERROR" if retryable else "PROVIDER_ERROR",
            public_message=(
                "Vision provider failed temporarily"
                if retryable
                else "Vision provider could not extract source evidence"
            ),
            retryable=retryable,
        ) from exc


class _VisionExtractionFailure(Exception):
    def __init__(self, *, code: str, public_message: str, retryable: bool):
        super().__init__(public_message)
        self.code = code
        self.public_message = public_message
        self.retryable = retryable


def _single_page_pdf_bytes(page) -> bytes:
    writer = pypdf.PdfWriter()
    writer.add_page(page)
    output = io.BytesIO()
    writer.write(output)
    return output.getvalue()


def _strict_json_object(raw: str) -> dict[str, Any]:
    stripped = raw.strip()
    stripped = re.sub(r"^```(?:json)?\s*", "", stripped)
    stripped = re.sub(r"\s*```$", "", stripped)
    value = json.loads(stripped, parse_float=Decimal)
    if not isinstance(value, dict):
        raise ValueError("provider response must be one JSON object")
    return value


def _build_result(
    source_format: SourceFormat,
    *,
    observations: list[ExtractedEvidence],
    units_attempted: int,
    units_completed: int,
    warnings: list[str] | None = None,
    errors: list[ExtractionError] | None = None,
) -> ExtractionResult:
    warnings = list(warnings or [])
    errors = list(errors or [])
    if not observations and not errors:
        errors.append(
            ExtractionError(
                code="NO_EVIDENCE",
                message="Extraction completed without any non-empty source observations",
            )
        )
    if observations and errors:
        status = ExtractionStatus.PARTIAL
    elif observations:
        status = ExtractionStatus.COMPLETE
    else:
        status = ExtractionStatus.FAILED
    return ExtractionResult(
        status=status,
        source_format=source_format,
        observations=tuple(observations),
        units_attempted=units_attempted,
        units_completed=units_completed,
        warnings=tuple(warnings),
        errors=tuple(errors),
    )


def _failed_result(
    source_format: SourceFormat,
    *,
    code: str,
    message: str,
    unit_key: str | None = None,
    provider: str | None = None,
    retryable: bool = False,
    units_attempted: int = 0,
) -> ExtractionResult:
    return ExtractionResult(
        status=ExtractionStatus.FAILED,
        source_format=source_format,
        observations=(),
        units_attempted=units_attempted,
        units_completed=0,
        errors=(
            ExtractionError(
                code=code,
                message=message,
                unit_key=unit_key,
                provider=provider,
                retryable=retryable,
            ),
        ),
    )


def _decode_delimited_text(content: bytes) -> tuple[str, str]:
    for encoding in ("utf-8-sig", "big5"):
        try:
            return content.decode(encoding), encoding
        except UnicodeDecodeError:
            continue
    raise UnicodeDecodeError("catalogue", content, 0, len(content), "unsupported text encoding")


def _sniff_dialect(text: str):
    sample = text[:8192]
    try:
        return csv.Sniffer().sniff(sample, delimiters=",;\t|")
    except csv.Error:
        return csv.excel


def _classify_source(filename: str, content_type: str) -> tuple[str, SourceFormat]:
    suffix = Path(filename or "").suffix.lower()
    normalized_type = (content_type or "").split(";", 1)[0].strip().lower()
    if suffix == ".xls":
        return "xls", SourceFormat.SPREADSHEET
    if suffix == ".xlsx":
        return "xlsx", SourceFormat.SPREADSHEET
    if suffix in {".csv", ".tsv"}:
        return "csv", SourceFormat.CSV
    if suffix == ".pdf":
        return "pdf", SourceFormat.PDF
    if suffix in {".jpg", ".jpeg"}:
        return "jpeg", SourceFormat.IMAGE
    if suffix == ".png":
        return "png", SourceFormat.IMAGE
    if suffix == ".txt":
        return "text", SourceFormat.TEXT
    if normalized_type == "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet":
        return "xlsx", SourceFormat.SPREADSHEET
    if normalized_type == "application/vnd.ms-excel":
        return "xls", SourceFormat.SPREADSHEET
    if normalized_type in {"text/csv", "application/csv"}:
        return "csv", SourceFormat.CSV
    if normalized_type == "application/pdf":
        return "pdf", SourceFormat.PDF
    if normalized_type == "image/jpeg":
        return "jpeg", SourceFormat.IMAGE
    if normalized_type == "image/png":
        return "png", SourceFormat.IMAGE
    if normalized_type.startswith("text/"):
        return "text", SourceFormat.TEXT
    return "unknown", SourceFormat.OTHER


def _pdf_text_is_reliable(text: str) -> bool:
    chars = [character for character in text if not character.isspace()]
    if not chars:
        return False

    def expected(character: str) -> bool:
        codepoint = ord(character)
        return (
            0x20 <= codepoint <= 0x7E
            or 0x00A0 <= codepoint <= 0x00FF
            or 0x2000 <= codepoint <= 0x206F
            or 0x3000 <= codepoint <= 0x303F
            or 0x3400 <= codepoint <= 0x4DBF
            or 0x4E00 <= codepoint <= 0x9FFF
            or 0xFF00 <= codepoint <= 0xFFEF
        )

    suspicious = sum(1 for character in chars if not expected(character))
    return Decimal(suspicious) / Decimal(len(chars)) < Decimal("0.12")


def _anthropic_api_key() -> str:
    return os.environ.get("ANTHROPIC_API_KEY", "").strip()


def _looks_transient(exc: Exception) -> bool:
    text = f"{type(exc).__name__}: {exc}".lower()
    return any(
        marker in text
        for marker in (
            "timeout",
            "timed out",
            "rate limit",
            "overloaded",
            "temporar",
            "connection",
            "503",
            "529",
        )
    )


__all__ = [
    "ExtractedEvidence",
    "ExtractionError",
    "ExtractionResult",
    "ExtractionStatus",
    "extract_evidence",
]
