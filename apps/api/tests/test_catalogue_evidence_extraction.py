"""Focused tests for the typed extraction boundary immediately before Raw."""

from __future__ import annotations

import io
import json
from decimal import Decimal

import openpyxl
import pypdf
from pypdf.generic import (
    DecodedStreamObject,
    DictionaryObject,
    NameObject,
)

from orchestration.catalogue_stage_adapter import raw_input_from_extracted_evidence
from schemas.catalogue_pipeline.enums import ExtractionMethod, SourceFormat
from services import catalogue_evidence_extraction as evidence_service
from services import catalogue_evidence_extraction
from services.catalogue_evidence_extraction import ExtractionStatus


def test_pdf_text_extraction_is_verbatim_source_located_and_keeps_duplicates(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    content = _pdf_with_pages(
        [
            "SKU | Description | Wholesale\n"
            "10447 | Healthy Cuisine Chicken 82g | HK$13.10\n"
            "10447 | Healthy Cuisine Chicken 82g | HK$13.10"
        ]
    )

    result = catalogue_evidence_extraction.extract_evidence(content, "hills.pdf", "application/pdf")

    assert result.status == ExtractionStatus.COMPLETE
    assert result.source_format == SourceFormat.PDF
    assert result.units_attempted == result.units_completed == 1
    assert [item.raw_text for item in result.observations] == [
        "SKU | Description | Wholesale",
        "10447 | Healthy Cuisine Chicken 82g | HK$13.10",
        "10447 | Healthy Cuisine Chicken 82g | HK$13.10",
    ]
    assert result.observations[1].observation_key == "page:1:line:2"
    assert result.observations[1].source_location.page_number == 1
    assert result.observations[1].extraction_method == ExtractionMethod.PDF_TEXT
    assert result.observations[1].provider == "pypdf"
    assert "cost_price" not in result.observations[1].model_dump()


def test_pdf_reports_partial_extraction_when_one_page_needs_unconfigured_vision(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    content = _pdf_with_pages(["10447 | Product | HK$13.10", None])

    result = catalogue_evidence_extraction.extract_evidence(content, "mixed.pdf", "application/pdf")

    assert result.status == ExtractionStatus.PARTIAL
    assert result.units_attempted == 2
    assert result.units_completed == 1
    assert len(result.observations) == 1
    assert result.errors[0].code == "EXTRACTION_CONFIGURATION_ERROR"
    assert result.errors[0].unit_key == "page:2"
    assert result.errors[0].provider == "anthropic"


def test_scanned_pdf_failure_is_operational_error_not_fake_catalogue_row(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    result = catalogue_evidence_extraction.extract_evidence(
        _pdf_with_pages([None]),
        "scan.pdf",
        "application/pdf",
    )

    assert result.status == ExtractionStatus.FAILED
    assert result.observations == ()
    assert result.errors[0].code == "EXTRACTION_CONFIGURATION_ERROR"


def test_spreadsheet_preserves_all_sheets_rows_cells_formulas_and_duplicates():
    workbook = openpyxl.Workbook()
    first = workbook.active
    first.title = "Price List"
    first.append(["Code", "Description", "Wholesale"])
    first.append(["10447", "Healthy Cuisine 82g", "=10+3.1"])
    first.append(["10447", "Healthy Cuisine 82g", "=10+3.1"])
    second = workbook.create_sheet("Terms")
    second.append(["MOQ", "6 bottles"])
    output = io.BytesIO()
    workbook.save(output)

    result = catalogue_evidence_extraction.extract_evidence(
        output.getvalue(),
        "catalogue.xlsx",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )

    assert result.status == ExtractionStatus.COMPLETE
    assert result.units_attempted == result.units_completed == 2
    assert [item.observation_key for item in result.observations] == [
        "sheet:Price List:row:1",
        "sheet:Price List:row:2",
        "sheet:Price List:row:3",
        "sheet:Terms:row:1",
    ]
    assert result.observations[1].source_location.cell_range == "A2:C2"
    assert result.observations[1].raw_cells[2].cell_reference == "C2"
    assert result.observations[1].raw_cells[2].raw_value == "=10+3.1"
    assert [cell.raw_value for cell in result.observations[2].raw_cells] == [
        cell.raw_value for cell in result.observations[1].raw_cells
    ]
    assert result.observations[3].source_location.sheet_name == "Terms"


def test_csv_preserves_coordinates_raw_values_empty_cells_and_duplicate_rows():
    content = (
        "\ufeffCode,Description,Wholesale,Notes\r\n"
        "10447,Healthy Cuisine 82g,HK$13.10,\r\n"
        "10447,Healthy Cuisine 82g,HK$13.10,\r\n"
    ).encode()

    result = catalogue_evidence_extraction.extract_evidence(content, "catalogue.csv", "text/csv")

    assert result.status == ExtractionStatus.COMPLETE
    assert result.source_format == SourceFormat.CSV
    assert result.units_attempted == result.units_completed == 3
    assert len(result.observations) == 3
    second = result.observations[1]
    assert second.source_location.row_number == 2
    assert second.source_location.cell_range == "A2:D2"
    assert second.raw_cells[2].raw_value == "HK$13.10"
    assert second.raw_cells[3].raw_value == ""
    assert [cell.raw_value for cell in result.observations[2].raw_cells] == [
        cell.raw_value for cell in second.raw_cells
    ]
    assert second.source_metadata == {"encoding": "utf-8-sig", "delimiter": ","}


def test_vision_extraction_records_actual_provider_metadata_and_png_media_type(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "configured-for-test")
    called: dict[str, str] = {}

    def fake_vision(content: bytes, *, media_type: str):
        called["media_type"] = media_type
        assert content == b"png-bytes"
        return evidence_service._VisionResponse(
            text=json.dumps(
                {
                    "observations": [
                        {
                            "raw_text": "ALF-10 | Syringe 10ml | HK$12.50",
                            "raw_cells": [],
                            "bounding_box": {
                                "x": 10,
                                "y": 20,
                                "width": 200,
                                "height": 24,
                                "unit": "px",
                            },
                            "confidence": "0.91",
                        }
                    ]
                }
            ),
            request_id="msg_test_123",
        )

    monkeypatch.setattr(evidence_service, "_call_anthropic_vision", fake_vision)

    result = catalogue_evidence_extraction.extract_evidence(b"png-bytes", "catalogue.png", "image/png")

    assert result.status == ExtractionStatus.COMPLETE
    assert called["media_type"] == "image/png"
    observation = result.observations[0]
    assert observation.extraction_method == ExtractionMethod.MODEL_VISION
    assert observation.provider == "anthropic"
    assert observation.provider_request_id == "msg_test_123"
    assert observation.model == evidence_service.ANTHROPIC_MODEL
    assert observation.confidence == Decimal("0.91")
    assert observation.source_location.bounding_box.width == Decimal("200")


def test_vision_response_rejects_semantic_product_fields(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "configured-for-test")

    def fake_vision(_content: bytes, *, media_type: str):
        assert media_type == "image/jpeg"
        return evidence_service._VisionResponse(
            text=json.dumps(
                {
                    "observations": [
                        {
                            "raw_text": "10447 | Product | HK$13.10",
                            "raw_cells": [],
                            "bounding_box": None,
                            "confidence": "0.9",
                            "cost_price": 13.1,
                        }
                    ]
                }
            )
        )

    monkeypatch.setattr(evidence_service, "_call_anthropic_vision", fake_vision)

    result = catalogue_evidence_extraction.extract_evidence(b"jpeg-bytes", "catalogue.jpg", "image/jpeg")

    assert result.status == ExtractionStatus.FAILED
    assert result.observations == ()
    assert result.errors[0].code == "MALFORMED_PROVIDER_RESPONSE"


def test_vision_response_rejects_normalized_numeric_raw_cells(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "configured-for-test")

    def fake_vision(_content: bytes, *, media_type: str):
        assert media_type == "image/jpeg"
        return evidence_service._VisionResponse(
            text=json.dumps(
                {
                    "observations": [
                        {
                            "raw_text": None,
                            "raw_cells": [
                                {
                                    "cell_reference": None,
                                    "row_number": None,
                                    "column_name": "Wholesale",
                                    "column_index": 1,
                                    "raw_value": 13.1,
                                }
                            ],
                            "bounding_box": None,
                            "confidence": "0.9",
                        }
                    ]
                }
            )
        )

    monkeypatch.setattr(evidence_service, "_call_anthropic_vision", fake_vision)

    result = catalogue_evidence_extraction.extract_evidence(b"jpeg-bytes", "catalogue.jpg", "image/jpeg")

    assert result.status == ExtractionStatus.FAILED
    assert result.errors[0].code == "MALFORMED_PROVIDER_RESPONSE"


def test_one_extracted_evidence_maps_to_one_raw_input_without_semantic_mutation():
    result = catalogue_evidence_extraction.extract_evidence(
        b"Code,Description,Wholesale\n10447,Healthy Cuisine 82g,HK$13.10\n",
        "catalogue.csv",
        "text/csv",
    )
    evidence = result.observations[1]

    raw_input = raw_input_from_extracted_evidence(evidence)

    assert raw_input.idempotency_key == evidence.observation_key
    assert raw_input.source_location == evidence.source_location
    assert raw_input.raw_text is None
    assert raw_input.raw_cells == evidence.raw_cells
    assert raw_input.extraction_method == ExtractionMethod.SPREADSHEET_CELL
    assert raw_input.source_metadata["observation_key"] == evidence.observation_key
    assert not hasattr(raw_input, "cost_price")


def test_empty_unknown_and_legacy_xls_sources_fail_explicitly():
    empty = catalogue_evidence_extraction.extract_evidence(b"", "empty.csv", "text/csv")
    unknown = catalogue_evidence_extraction.extract_evidence(b"data", "catalogue.bin", "application/octet-stream")
    legacy_xls = catalogue_evidence_extraction.extract_evidence(b"data", "catalogue.xls", "application/vnd.ms-excel")
    mislabeled_csv = catalogue_evidence_extraction.extract_evidence(
        b"Code,Description\n10447,Product\n",
        "catalogue.csv",
        "application/vnd.ms-excel",
    )

    assert empty.status == ExtractionStatus.FAILED
    assert empty.errors[0].code == "EMPTY_SOURCE"
    assert unknown.status == ExtractionStatus.FAILED
    assert unknown.errors[0].code == "UNSUPPORTED_SOURCE_FORMAT"
    assert legacy_xls.status == ExtractionStatus.FAILED
    assert legacy_xls.errors[0].code == "UNSUPPORTED_LEGACY_XLS"
    assert mislabeled_csv.status == ExtractionStatus.COMPLETE
    assert mislabeled_csv.source_format == SourceFormat.CSV


def _pdf_with_pages(page_texts: list[str | None]) -> bytes:
    writer = pypdf.PdfWriter()
    for text in page_texts:
        page = writer.add_blank_page(width=612, height=792)
        if text:
            _write_text_to_page(writer, page, text)
    output = io.BytesIO()
    writer.write(output)
    return output.getvalue()


def _write_text_to_page(writer: pypdf.PdfWriter, page, text: str) -> None:
    font = DictionaryObject(
        {
            NameObject("/Type"): NameObject("/Font"),
            NameObject("/Subtype"): NameObject("/Type1"),
            NameObject("/BaseFont"): NameObject("/Helvetica"),
        }
    )
    font_ref = writer._add_object(font)
    page[NameObject("/Resources")] = DictionaryObject(
        {NameObject("/Font"): DictionaryObject({NameObject("/F1"): font_ref})}
    )
    parts = ["BT", "/F1 10 Tf", "36 750 Td", "14 TL"]
    for line in text.splitlines():
        parts.append(f"({_escape_pdf_text(line)}) Tj")
        parts.append("T*")
    parts.append("ET")
    stream = DecodedStreamObject()
    stream.set_data("\n".join(parts).encode())
    page[NameObject("/Contents")] = writer._add_object(stream)


def _escape_pdf_text(text: str) -> str:
    return text.replace("\\", r"\\").replace("(", r"\(").replace(")", r"\)")
