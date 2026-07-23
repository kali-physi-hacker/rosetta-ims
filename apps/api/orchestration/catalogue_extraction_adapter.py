"""Source-evidence extraction adapter for catalogue orchestration."""

from __future__ import annotations

import io
import re
from decimal import Decimal, InvalidOperation
from typing import Any
from uuid import UUID

import pypdf

from schemas.catalogue_pipeline.enums import ExtractionMethod, UnitCode
from services import extraction_service

from .catalogue_types import (
    ExtractedCatalogueRow,
    ExtractionEvidenceError,
    ExtractionEvidenceResult,
    TransientExtractionError,
    VerifiedSourceAsset,
)


MODEL_NAME = "claude-haiku"
MODEL_VERSION = "claude-haiku-4-5-20251001"


def extract_source_evidence(source: VerifiedSourceAsset, runtime_contract) -> ExtractionEvidenceResult:
    """Extract source-located evidence for the currently supported source formats."""

    source_format = runtime_contract.declaration.source_structure.source_format.value
    if source_format not in {"PDF", "PDF_TABLE"}:
        raise ExtractionEvidenceError("Only evidence-backed PDF source contracts are currently runtime-orchestrated")
    return _extract_pdf_with_page_evidence(source, runtime_contract)


def staging_payload_from_extracted_row(
    row: ExtractedCatalogueRow,
    *,
    raw_observation_id: UUID,
    runtime_contract,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Build raw/proposed staging payloads without inventing unresolved semantics."""

    fields = row.extracted_fields
    raw_fields = {
        "supplier_sku": _text(fields.get("supplier_sku")),
        "product_name": _text(fields.get("description")),
        "original_product_name": _text(fields.get("original_description")),
        "brand": _text(fields.get("brand")),
        "category": _text(fields.get("category")),
        "cost": _raw_money_text(fields.get("cost_price")),
        "packaging": _text(fields.get("pack_size") or fields.get("uom")),
        "mbb_text": _text(fields.get("bulk_buy_tiers")),
        "barcode": _text(fields.get("barcode")),
        "variant": _text(fields.get("variant")),
        "source_row_label": row.row_key,
    }
    evidence = {
        "raw_observation_id": str(raw_observation_id),
        "field_path": "/raw_text",
        "confidence": str(row.extraction_confidence) if row.extraction_confidence is not None else None,
    }
    proposed: dict[str, Any] = {"mbb_terms": []}
    for source_key, proposed_key in (
        ("supplier_sku", "supplier_sku"),
        ("description", "product_name"),
        ("brand", "brand"),
        ("category", "category"),
        ("barcode", "barcode"),
        ("variant", "variant"),
    ):
        value = _text(fields.get(source_key))
        if value is not None:
            proposed[proposed_key] = {"value": value, "evidence": evidence}

    cost = _cost_proposal(fields.get("cost_price"), runtime_contract, evidence)
    if cost is not None:
        proposed["cost"] = cost

    packaging = _packaging_proposal(fields, runtime_contract, evidence)
    if packaging is not None:
        proposed["packaging"] = packaging

    return raw_fields, proposed


def _extract_pdf_with_page_evidence(source: VerifiedSourceAsset, runtime_contract) -> ExtractionEvidenceResult:
    try:
        reader = pypdf.PdfReader(io.BytesIO(source.content))
    except Exception as exc:
        raise ExtractionEvidenceError("PDF source cannot be read for page-addressed extraction") from exc
    rows: list[ExtractedCatalogueRow] = []
    warnings: list[str] = []
    rejected = 0
    for page_index, page in enumerate(reader.pages, start=1):
        page_text = page.extract_text() or ""
        page_content = _single_page_pdf_bytes(page)
        try:
            items, _fmt = extraction_service.extract(
                page_content,
                f"{source.original_filename}#page-{page_index}.pdf",
                "application/pdf",
                contract=runtime_contract,
            )
        except Exception as exc:
            if _looks_transient(exc):
                raise TransientExtractionError("Temporary extraction provider failure") from exc
            raise ExtractionEvidenceError("Extraction provider failed for a source page") from exc
        for row_index, item in enumerate(items or [], start=1):
            try:
                rows.append(_row_from_item(item, page_number=page_index, row_index=row_index, page_text=page_text))
            except ExtractionEvidenceError as exc:
                rejected += 1
                warnings.append(f"page {page_index} row {row_index}: {exc.public_message()}")
    if not rows:
        if warnings:
            raise ExtractionEvidenceError("Extraction produced no truthful rows; rejected rows were recorded")
        raise ExtractionEvidenceError("Extraction produced no rows")
    return ExtractionEvidenceResult(rows=tuple(rows), rejected_count=rejected, warnings=tuple(warnings))


def _single_page_pdf_bytes(page) -> bytes:
    writer = pypdf.PdfWriter()
    writer.add_page(page)
    output = io.BytesIO()
    writer.write(output)
    return output.getvalue()


def _row_from_item(item: Any, *, page_number: int, row_index: int, page_text: str) -> ExtractedCatalogueRow:
    if not isinstance(item, dict):
        raise ExtractionEvidenceError("extracted row is not an object")
    if item.get("_stub") or _error_description(item.get("description")):
        raise ExtractionEvidenceError("extractor returned a stub or error placeholder")
    raw_text = _text(item.get("_raw_text") or item.get("raw_text") or page_text)
    raw_cells = tuple(item.get("_raw_cells") or item.get("raw_cells") or ())
    if not raw_text and not raw_cells:
        raise ExtractionEvidenceError("extracted row lacks raw source evidence")
    cleaned = {
        key: value
        for key, value in item.items()
        if not key.startswith("_") and key not in {"raw_text", "raw_cells"}
    }
    confidence = _confidence(item.get("confidence"))
    row_key = f"page:{page_number}:row:{row_index}"
    return ExtractedCatalogueRow(
        row_key=row_key,
        source_location={"page_number": page_number, "source_object_key": row_key},
        raw_text=raw_text,
        raw_cells=tuple(raw_cells),
        extracted_fields=cleaned,
        extraction_method=ExtractionMethod.MODEL_TEXT.value,
        extraction_model=MODEL_NAME,
        extraction_model_version=MODEL_VERSION,
        extraction_confidence=confidence,
    )


def _cost_proposal(value: Any, runtime_contract, evidence: dict[str, Any]) -> dict[str, Any] | None:
    amount = _decimal_or_none(value)
    basis = runtime_contract.declaration.pricing.price_basis
    if amount is None or basis is None or basis.code is None:
        return None
    return {
        "amount": str(amount),
        "currency": "HKD",
        "price_basis": basis.model_dump(mode="json"),
        "evidence": evidence,
    }


def _packaging_proposal(fields: dict[str, Any], runtime_contract, evidence: dict[str, Any]) -> dict[str, Any] | None:
    source_text = _text(fields.get("pack_size") or fields.get("uom"))
    semantics = runtime_contract.declaration.packaging
    if not source_text and semantics.price_basis is None:
        return None
    proposal: dict[str, Any] = {"source_text": source_text, "evidence": evidence}
    if semantics.price_basis is not None:
        proposal["price_basis"] = semantics.price_basis.model_dump(mode="json")
    content = _content_measure(source_text)
    if content:
        amount, uom = content
        proposal["content_amount"] = str(amount)
        proposal["content_uom"] = {"code": uom}
    order_increment = _decimal_or_none(fields.get("order_increment_qty"))
    if order_increment is not None and semantics.price_basis is not None and semantics.price_basis.code is not None:
        proposal["order_increment"] = {
            "amount": str(order_increment),
            "uom": semantics.price_basis.model_dump(mode="json"),
        }
    return proposal


def _content_measure(text: str | None) -> tuple[Decimal, str] | None:
    if not text:
        return None
    match = re.search(r"(?<!\d)(\d+(?:\.\d+)?)\s*(ml|mL|ML|g|G|kg|KG|l|L)\b", text)
    if not match:
        return None
    amount = Decimal(match.group(1))
    raw_uom = match.group(2).upper()
    uom = {
        "ML": UnitCode.ML.value,
        "G": UnitCode.G.value,
        "KG": UnitCode.KG.value,
        "L": UnitCode.L.value,
    }[raw_uom]
    return amount, uom


def _confidence(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        confidence = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ExtractionEvidenceError("extraction confidence is not decimal-compatible") from exc
    if confidence < 0 or confidence > 1:
        raise ExtractionEvidenceError("extraction confidence is outside [0, 1]")
    return confidence


def _decimal_or_none(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    if isinstance(value, str) and value.strip().lower() in {"by quote", "quote", "n/a", "na"}:
        return None
    try:
        decimal = Decimal(str(value).replace(",", "").replace("$", "").replace("HKD", "").replace("HK$", "").strip())
    except (InvalidOperation, ValueError):
        return None
    return decimal if decimal >= 0 else None


def _raw_money_text(value: Any) -> str | None:
    if value is None or value == "":
        return None
    return str(value)


def _text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _error_description(value: Any) -> bool:
    text = _text(value) or ""
    lowered = text.lower()
    return (
        lowered.startswith("[ai extraction disabled")
        or lowered.startswith("[extraction error")
        or lowered.startswith("extraction error")
        or lowered.startswith("vision extraction error")
    )


def _looks_transient(exc: Exception) -> bool:
    text = f"{type(exc).__name__}: {exc}".lower()
    return any(marker in text for marker in ("timeout", "rate", "temporar", "connection", "429", "503"))
