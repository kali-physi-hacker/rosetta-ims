"""Focused tests for typed Staging extraction after Raw completion."""

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
from services import catalogue_vision_provider as vision_provider
from services import catalogue_evidence_extraction
from services.catalogue_evidence_extraction import ExtractionStatus


def test_scanned_pdf_failure_is_operational_error_not_fake_catalogue_row(monkeypatch):
    for _k in ("ANTHROPIC_API_KEY", "GEMINI_API_KEY", "GOOGLE_API_KEY"): monkeypatch.delenv(_k, raising=False)

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
    # Formula cells preserve the formula; raw_value carries the workbook's
    # cached displayed value when one exists. This in-memory workbook was never
    # opened by a spreadsheet app, so there is no cache and raw_value falls
    # back to the formula string.
    assert result.observations[1].raw_cells[2].formula == "=10+3.1"
    assert result.observations[1].raw_cells[2].raw_value == "=10+3.1"
    assert result.observations[1].raw_cells[0].formula is None
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
    # CSV parsing is one independently complete unit; rows are observations.
    assert result.units_attempted == result.units_completed == 1
    assert result.unit_outcomes[0].unit_key == "csv:1"
    assert result.unit_outcomes[0].observation_count == 3
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
                    "page_outcome": "evidence",
                    "columns": [],
                    "rows": [
                        {
                            "text": "ALF-10 | Syringe 10ml | HK$12.50",
                            "box": [10, 20, 200, 24],
                            "confidence": "0.91",
                        }
                    ],
                }
            ),
            request_id="msg_test_123",
        )

    monkeypatch.setattr(evidence_service, "_call_vision", fake_vision)

    result = catalogue_evidence_extraction.extract_evidence(b"png-bytes", "catalogue.png", "image/png")

    assert result.status == ExtractionStatus.COMPLETE
    assert called["media_type"] == "image/png"
    observation = result.observations[0]
    assert observation.extraction_method == ExtractionMethod.MODEL_VISION
    assert observation.provider == "anthropic"
    assert observation.provider_request_id == "msg_test_123"
    assert observation.model == vision_provider.DEFAULT_ANTHROPIC_MODEL
    assert observation.confidence == Decimal("0.91")
    assert observation.source_location.bounding_box.width == Decimal("200")


def test_vision_response_rejects_semantic_product_fields(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "configured-for-test")

    def fake_vision(_content: bytes, *, media_type: str):
        assert media_type == "image/jpeg"
        return evidence_service._VisionResponse(
            text=json.dumps(
                {
                    "page_outcome": "evidence",
                    "columns": [],
                    "rows": [
                        {
                            "text": "10447 | Product | HK$13.10",
                            "confidence": "0.9",
                            "cost_price": 13.1,
                        }
                    ],
                }
            )
        )

    monkeypatch.setattr(evidence_service, "_call_vision", fake_vision)

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
                    "page_outcome": "evidence",
                    "columns": ["Wholesale"],
                    "rows": [{"cells": [13.1], "confidence": "0.9"}],
                }
            )
        )

    monkeypatch.setattr(evidence_service, "_call_vision", fake_vision)

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


# ── Page extraction policy (Fix 1) + typed vision outcomes (Fix 2) ──────────

from pypdf.generic import NumberObject  # noqa: E402


def _add_image_xobject(writer: pypdf.PdfWriter, page, *, width: int = 1700, height: int = 2200) -> None:
    image = DecodedStreamObject()
    image.set_data(b"\x00")
    image.update(
        {
            NameObject("/Type"): NameObject("/XObject"),
            NameObject("/Subtype"): NameObject("/Image"),
            NameObject("/Width"): NumberObject(width),
            NameObject("/Height"): NumberObject(height),
            NameObject("/ColorSpace"): NameObject("/DeviceGray"),
            NameObject("/BitsPerComponent"): NumberObject(8),
        }
    )
    reference = writer._add_object(image)
    resources = page.get(NameObject("/Resources"))
    if resources is None:
        resources = DictionaryObject()
        page[NameObject("/Resources")] = resources
    resources[NameObject("/XObject")] = DictionaryObject({NameObject("/Im1"): reference})


def _pdf_pages(pages: list[dict]) -> bytes:
    """Build a PDF from page specs: {"text": str | None, "image": bool}."""

    writer = pypdf.PdfWriter()
    for spec in pages:
        page = writer.add_blank_page(width=612, height=792)
        if spec.get("text"):
            _write_text_to_page(writer, page, spec["text"])
        if spec.get("image"):
            _add_image_xobject(writer, page, width=spec.get("image_width", 1700), height=spec.get("image_height", 2200))
    output = io.BytesIO()
    writer.write(output)
    return output.getvalue()


def _vision_stub(payloads_by_call: list[dict]):
    calls = {"count": 0}

    def fake_vision(_content: bytes, *, media_type: str):
        index = min(calls["count"], len(payloads_by_call) - 1)
        calls["count"] += 1
        return evidence_service._VisionResponse(
            text=json.dumps(payloads_by_call[index]), request_id=f"msg_{calls['count']}"
        )

    return fake_vision, calls


_EVIDENCE_PAYLOAD = {
    "page_outcome": "evidence",
    "columns": [],
    "rows": [
        {
            "text": "SCANNED-1 | Scanned Product 500g | HK$99.00",
            "box": [5, 40, 300, 20],
            "confidence": "0.9",
        }
    ],
}


def test_garbled_or_unreliable_text_layer_is_classified_for_vision():
    # A text layer dominated by unexpected code points is unreliable and must
    # route to vision with the text discarded — tested directly on the page
    # policy because a synthetic PDF writer cannot reliably round-trip a
    # genuinely garbled glyph stream.
    garbled = chr(0x0450) * 40  # Cyrillic block, outside the expected ranges
    assert evidence_service._pdf_text_is_reliable(garbled) is False
    decision = evidence_service._classify_pdf_page(None, garbled)
    assert decision.keep_text is False
    assert decision.vision_required is True

    empty_decision = evidence_service._classify_pdf_page(None, "   \n  ")
    assert empty_decision.keep_text is False
    assert empty_decision.vision_required is True


def test_empty_vision_array_without_outcome_fails_the_page(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "configured-for-test")
    fake_vision, _ = _vision_stub([{"rows": []}])
    monkeypatch.setattr(evidence_service, "_call_vision", fake_vision)
    content = _pdf_pages([{"text": None, "image": True}])

    result = catalogue_evidence_extraction.extract_evidence(content, "empty.pdf", "application/pdf")

    assert result.status == ExtractionStatus.FAILED
    assert result.units_completed == 0
    assert result.errors[0].code == "MALFORMED_PROVIDER_RESPONSE"
    assert result.errors[0].unit_key == "page:1"


def test_evidence_outcome_with_empty_array_is_malformed_not_empty_page(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "configured-for-test")
    fake_vision, _ = _vision_stub([{"page_outcome": "evidence", "rows": []}])
    monkeypatch.setattr(evidence_service, "_call_vision", fake_vision)

    result = catalogue_evidence_extraction.extract_evidence(b"jpeg-bytes", "catalogue.jpg", "image/jpeg")

    assert result.status == ExtractionStatus.FAILED
    assert result.errors[0].code == "MALFORMED_PROVIDER_RESPONSE"


def test_explicit_no_catalogue_evidence_page_is_accounted_without_fake_observations(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "configured-for-test")
    fake_vision, _ = _vision_stub([{"page_outcome": "no_catalogue_evidence", "rows": []}])
    monkeypatch.setattr(evidence_service, "_call_vision", fake_vision)
    content = _pdf_pages([{"text": None, "image": True}])

    result = catalogue_evidence_extraction.extract_evidence(content, "cover.pdf", "application/pdf")

    assert result.status == ExtractionStatus.COMPLETE
    assert result.observations == ()
    assert result.units_attempted == result.units_completed == 1
    assert result.empty_units == 1
    assert any("no catalogue evidence" in warning for warning in result.warnings)


def test_no_catalogue_evidence_with_observations_is_malformed(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "configured-for-test")
    payload = {"page_outcome": "no_catalogue_evidence", "rows": _EVIDENCE_PAYLOAD["rows"]}
    fake_vision, _ = _vision_stub([payload])
    monkeypatch.setattr(evidence_service, "_call_vision", fake_vision)

    result = catalogue_evidence_extraction.extract_evidence(b"jpeg-bytes", "catalogue.jpg", "image/jpeg")

    assert result.status == ExtractionStatus.FAILED
    assert result.errors[0].code == "MALFORMED_PROVIDER_RESPONSE"


def test_vision_observation_identity_is_stable_across_reordered_retries():
    def _payload(rows):
        return json.dumps(
            {
                "page_outcome": "evidence",
                "columns": [],
                "rows": [{"text": row, "confidence": "0.9"} for row in rows],
            }
        )

    rows = ["ROW-A | Product A | HK$1.00", "ROW-B | Product B | HK$2.00", "ROW-B | Product B | HK$2.00"]
    first = evidence_service._VisionResponse(text=_payload(rows), request_id="msg_a")
    reordered = evidence_service._VisionResponse(text=_payload(list(reversed(rows))), request_id="msg_b")

    observations_a, _ = evidence_service._vision_observations(
        first, extraction_method=ExtractionMethod.MODEL_VISION, unit_key="page:1", page_number=1
    )
    observations_b, _ = evidence_service._vision_observations(
        reordered, extraction_method=ExtractionMethod.MODEL_VISION, unit_key="page:1", page_number=1
    )

    keys_a = {o.observation_key for o in observations_a}
    keys_b = {o.observation_key for o in observations_b}
    assert keys_a == keys_b, "identical evidence must keep identical identities across reordered retries"
    assert len(keys_a) == 3  # the duplicate row keeps a distinct ordinal identity


def test_provider_failure_classification_reads_either_vendors_http_status():
    """Both SDKs report the status; the pipeline's retry policy is one rule.

    google-genai puts it on `.code`, Anthropic on `.status_code` — and 529
    (overloaded) is Anthropic's, which must read as retryable like any 5xx.
    """

    class _GoogleError(Exception):
        def __init__(self, code):
            super().__init__(f"gemini {code}")
            self.code = code

    class _AnthropicError(Exception):
        def __init__(self, status_code):
            super().__init__(f"anthropic {status_code}")
            self.status_code = status_code

    for error in (_GoogleError, _AnthropicError):
        rate_limited = vision_provider.classify_provider_failure(error(429))
        assert rate_limited.retryable is True
        assert rate_limited.code == "TRANSIENT_PROVIDER_ERROR"

        assert vision_provider.classify_provider_failure(error(503)).retryable is True
        assert vision_provider.classify_provider_failure(error(529)).retryable is True

        unauthorized = vision_provider.classify_provider_failure(error(401))
        assert unauthorized.retryable is False
        assert unauthorized.code == "EXTRACTION_CONFIGURATION_ERROR"

        bad_request = vision_provider.classify_provider_failure(error(400))
        assert bad_request.retryable is False
        assert bad_request.code == "PROVIDER_ERROR"

    # Network-level failures have no HTTP status -> conservative message heuristic.
    timeout = vision_provider.classify_provider_failure(TimeoutError("deadline exceeded / connection timeout"))
    assert timeout.retryable is True


# ── Stage 3 architectural boundary: extraction must not reach interpretation
#    or any later stage; Anthropic is allowed only in the provider seam. ─────

import ast  # noqa: E402
from pathlib import Path  # noqa: E402


def _import_closure(seed_modules: list[str]) -> set[str]:
    backend_root = Path(__file__).resolve().parent.parent

    def _local_path(module_name: str) -> Path | None:
        as_file = backend_root / (module_name.replace(".", "/") + ".py")
        if as_file.exists():
            return as_file
        as_package = backend_root / module_name.replace(".", "/") / "__init__.py"
        return as_package if as_package.exists() else None

    def _imports_of(path: Path, module_name: str) -> set[str]:
        package = module_name if path.name == "__init__.py" else module_name.rsplit(".", 1)[0]
        names: set[str] = set()
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.Import):
                names.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                if node.level:
                    names.add(f"{package}.{node.module}" if node.module else package)
                elif node.module:
                    names.add(node.module)
        return names

    visited: set[str] = set()
    queue = list(seed_modules)
    while queue:
        module_name = queue.pop()
        if module_name in visited:
            continue
        visited.add(module_name)
        path = _local_path(module_name)
        if path is None:
            continue
        queue.extend(_imports_of(path, module_name))
    return visited


def test_stage3_extraction_import_boundary():
    closure = _import_closure(
        [
            "services.catalogue_evidence_extraction",
            "orchestration.catalogue_extraction_adapter",
        ]
    )
    forbidden = {
        "services.catalogue_conformance",       # Intermediate layer
        "services.catalogue_pipeline_stages",      # staging/validation/mastering/serving services
        "services.tagging_service",
        "services.sku_service",
        "services.pricing_service",
    }
    hits = forbidden & closure
    assert not hits, f"Stage 3 extraction closure reaches forbidden modules: {sorted(hits)}"


def test_vision_provider_is_reachable_only_through_the_stage3_provider_seam():
    # The extraction ENVELOPE/adapter must not import the vision provider (Gemini)
    # directly; the client lives behind the seam inside catalogue_evidence_extraction.
    adapter_closure = _import_closure(["orchestration.catalogue_extraction_adapter"])
    assert "services.catalogue_evidence_extraction" in adapter_closure

    backend_root = Path(__file__).resolve().parent.parent
    seam = (backend_root / "services" / "catalogue_evidence_extraction.py").read_text()
    tree = ast.parse(seam)
    # No AI provider is imported at module top level — neither client (anthropic,
    # google.genai) loads until a page is actually sent.
    top_level = set()
    for node in tree.body:
        if isinstance(node, ast.Import):
            top_level.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            top_level.add(node.module.split(".")[0])
    assert "google" not in top_level, "vision provider client must stay behind the function-level seam"
    assert "anthropic" not in top_level
    # Same rule inside the provider module, which is where the clients now live.
    provider_tree = ast.parse((backend_root / "services" / "catalogue_vision_provider.py").read_text())
    provider_top = set()
    for node in provider_tree.body:
        if isinstance(node, ast.Import):
            provider_top.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            provider_top.add(node.module.split(".")[0])
    assert not {"google", "anthropic"} & provider_top, "clients load lazily, inside the call"


# ── Fix 1 follow-up: typed page modes, decorative vs material images ────────

from services.catalogue_evidence_extraction import PdfPageMode  # noqa: E402


def _first_page_decision(content: bytes):
    reader = pypdf.PdfReader(io.BytesIO(content))
    page = reader.pages[0]
    return evidence_service._classify_pdf_page(page, page.extract_text() or "")


_RICH_TEXT = (
    "Supplier Catalogue 2026\n"
    "Wholesale Price List Terms and Conditions\n"
    "All prices quoted exclude delivery charges\n"
    "Contact your account manager for volume enquiries"
)


def test_image_with_unknown_dimensions_is_uncertain_and_requires_vision(monkeypatch):
    for _k in ("ANTHROPIC_API_KEY", "GEMINI_API_KEY", "GOOGLE_API_KEY"): monkeypatch.delenv(_k, raising=False)
    # Build a page whose image XObject has no /Width//Height: coverage unknowable.
    writer = pypdf.PdfWriter()
    page = writer.add_blank_page(width=612, height=792)
    _write_text_to_page(writer, page, _RICH_TEXT)
    image = DecodedStreamObject()
    image.set_data(b"\x00")
    image.update(
        {
            NameObject("/Type"): NameObject("/XObject"),
            NameObject("/Subtype"): NameObject("/Image"),
        }
    )
    reference = writer._add_object(image)
    resources = page.get(NameObject("/Resources"))
    resources[NameObject("/XObject")] = DictionaryObject({NameObject("/Im1"): reference})
    output = io.BytesIO()
    writer.write(output)
    content = output.getvalue()

    decision = _first_page_decision(content)
    assert decision.mode == PdfPageMode.UNCERTAIN
    assert decision.reason == "IMAGE_COVERAGE_UNKNOWN"
    assert decision.vision_required is True

    result = catalogue_evidence_extraction.extract_evidence(content, "unknown.pdf", "application/pdf")
    # Three meaningful text lines cannot silently complete the page.
    assert result.status != ExtractionStatus.COMPLETE
    assert result.units_completed == 0


def test_mid_size_image_is_treated_as_coverage_unknown(monkeypatch):
    for _k in ("ANTHROPIC_API_KEY", "GEMINI_API_KEY", "GOOGLE_API_KEY"): monkeypatch.delenv(_k, raising=False)
    content = _pdf_pages([{"text": _RICH_TEXT, "image": True, "image_width": 450, "image_height": 450}])

    decision = _first_page_decision(content)
    assert decision.mode == PdfPageMode.UNCERTAIN
    assert decision.reason == "IMAGE_COVERAGE_UNKNOWN"


# ── PDF evidence now routes every page to the vision provider ────────────────
# The PDF text layer is no longer used as evidence: every page is sent to the
# vision provider so tabular pages yield column-labeled cells the supplier
# contract can map deterministically. These tests stub the provider seam and
# never call real Gemini.


def _vision_envelope(rows: list[dict[str, str]]) -> str:
    """Compact vision envelope: columns once, positional row cells."""

    columns: list[str] = []
    for row in rows:
        for column in row:
            if column not in columns:
                columns.append(column)
    return json.dumps(
        {
            "page_outcome": "evidence",
            "columns": columns,
            "rows": [
                {"cells": [row.get(column) for column in columns], "box": [0, 0, 1, 1], "confidence": "0.95"}
                for row in rows
            ],
        }
    )


def test_pdf_pages_are_extracted_via_vision_into_column_labeled_cells(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "configured-for-test")
    seen: dict[str, str] = {}

    def fake_vision(content: bytes, *, media_type: str):
        seen["media_type"] = media_type
        return evidence_service._VisionResponse(
            text=_vision_envelope([{"Product Code": "10447", "Size": "82g"}]),
            request_id="msg_pdf_1",
        )

    monkeypatch.setattr(evidence_service, "_call_vision", fake_vision)
    content = _pdf_with_pages(["Hills Catalogue"])

    result = catalogue_evidence_extraction.extract_evidence(content, "hills.pdf", "application/pdf")

    assert result.status == ExtractionStatus.COMPLETE
    assert result.source_format == SourceFormat.PDF
    assert seen["media_type"] == "application/pdf"  # the page is sent as PDF bytes
    assert result.units_attempted == result.units_completed == 1
    assert len(result.observations) == 1
    observation = result.observations[0]
    assert observation.extraction_method == ExtractionMethod.MODEL_VISION
    assert observation.provider == "anthropic"
    assert observation.provider_request_id == "msg_pdf_1"
    assert observation.source_location.page_number == 1
    assert observation.confidence == Decimal("0.95")
    assert [(cell.column_name, cell.raw_value) for cell in observation.raw_cells] == [
        ("Product Code", "10447"),
        ("Size", "82g"),
    ]


def test_vision_envelope_preserves_multiple_tables_and_document_level_text():
    response = evidence_service._VisionResponse(
        text=json.dumps(
            {
                "page_outcome": "evidence",
                "tables": [
                    {
                        "columns": ["Product Code", "Wholesale"],
                        "rows": [{"cells": ["10447", "HK$13.10"]}],
                    },
                    {
                        "columns": ["Service Code", "Fee Basis"],
                        "rows": [{"cells": ["DELIVERY", "per order"]}],
                    },
                ],
                "text_observations": [
                    {"text": "Prices effective from 1 August 2026"},
                    {"text": "Minimum order: 12 cases"},
                ],
            }
        ),
        request_id="multi-table",
    )

    observations, outcome = evidence_service._vision_observations(
        response,
        extraction_method=ExtractionMethod.MODEL_VISION,
        unit_key="page:1",
        page_number=1,
    )

    assert outcome == "evidence"
    assert len(observations) == 4
    assert [(cell.column_name, cell.raw_value) for cell in observations[0].raw_cells] == [
        ("Product Code", "10447"),
        ("Wholesale", "HK$13.10"),
    ]
    assert [(cell.column_name, cell.raw_value) for cell in observations[1].raw_cells] == [
        ("Service Code", "DELIVERY"),
        ("Fee Basis", "per order"),
    ]
    assert observations[2].raw_text == "Prices effective from 1 August 2026"
    assert observations[3].raw_text == "Minimum order: 12 cases"


def test_pdf_retries_only_the_transiently_failed_page(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "configured-for-test")
    calls = 0

    def transient_then_success(_content: bytes, *, media_type: str):
        nonlocal calls
        calls += 1
        assert media_type == "application/pdf"
        if calls == 1:
            raise evidence_service._VisionExtractionFailure(
                code="TRANSIENT_PROVIDER_ERROR",
                public_message="Vision provider failed temporarily",
                retryable=True,
            )
        return evidence_service._VisionResponse(
            text=_vision_envelope([{"Product Code": "10447"}]),
            request_id="retry-success",
        )

    monkeypatch.setattr(
        evidence_service,
        "_call_vision",
        transient_then_success,
    )

    result = catalogue_evidence_extraction.extract_evidence(
        _pdf_with_pages(["Hills Catalogue"]),
        "hills.pdf",
        "application/pdf",
    )

    assert result.status == ExtractionStatus.COMPLETE
    assert calls == 2
    assert result.unit_outcomes[0].status.value == "EVIDENCE_CAPTURED"
    assert result.unit_outcomes[0].attempt_count == 2


def test_pdf_without_configured_vision_provider_is_a_configuration_error(monkeypatch):
    for _key in ("ANTHROPIC_API_KEY", "GEMINI_API_KEY", "GOOGLE_API_KEY"):
        monkeypatch.delenv(_key, raising=False)
    content = _pdf_with_pages(["Product Code | Size\n10447 | 82g"])

    result = catalogue_evidence_extraction.extract_evidence(content, "hills.pdf", "application/pdf")

    assert result.status == ExtractionStatus.FAILED
    assert result.observations == ()
    assert result.errors[0].code == "EXTRACTION_CONFIGURATION_ERROR"
    assert result.errors[0].provider == "anthropic"
    assert result.errors[0].message == "PDF evidence extraction requires a configured anthropic vision provider"


def test_csv_cells_carry_column_name_from_header_row():
    content = (
        "Code,Description,Wholesale\r\n"
        "10447,Healthy Cuisine 82g,HK$13.10\r\n"
    ).encode()

    result = catalogue_evidence_extraction.extract_evidence(content, "catalogue.csv", "text/csv")

    assert result.status == ExtractionStatus.COMPLETE
    # The first non-empty row is recognised as the header and its labels are
    # attached to every data cell by column, so the supplier contract can map
    # each raw value to its printed column heading.
    assert [cell.column_name for cell in result.observations[0].raw_cells] == ["Code", "Description", "Wholesale"]
    data_cells = result.observations[1].raw_cells
    assert [(cell.column_name, cell.raw_value) for cell in data_cells] == [
        ("Code", "10447"),
        ("Description", "Healthy Cuisine 82g"),
        ("Wholesale", "HK$13.10"),
    ]


def test_strict_json_object_recovers_trailing_structural_debris():
    """Live JSON-mode responses occasionally append a stray closing brace after
    a complete envelope (observed on real Alfamedic pages). Structural debris is
    tolerated; real trailing content still fails."""
    from services.catalogue_evidence_extraction import _strict_json_object

    import pytest

    payload = _strict_json_object('{"page_outcome": "no_catalogue_evidence", "observations": []}\n}')
    assert payload["page_outcome"] == "no_catalogue_evidence"

    with pytest.raises(Exception):
        _strict_json_object('{"page_outcome": "evidence", "observations": []} {"second": "object"}')


def test_retry_backoff_sleeps_linearly_then_succeeds(monkeypatch):
    """Backoff sequence: base x1 before retry 1, base x2 before retry 2; no
    sleep on success and none after a permanent failure."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "configured-for-test")
    monkeypatch.setattr(evidence_service, "_VISION_RETRY_BACKOFF_SECONDS", 7.0)
    sleeps: list[float] = []
    monkeypatch.setattr(evidence_service.time, "sleep", lambda s: sleeps.append(s))

    calls = {"n": 0}

    def flaky_vision(_content: bytes, *, media_type: str):
        calls["n"] += 1
        if calls["n"] <= 2:
            raise evidence_service._VisionExtractionFailure(
                code="TRANSIENT_PROVIDER_ERROR", public_message="throttled", retryable=True
            )
        return evidence_service._VisionResponse(text=json.dumps(_EVIDENCE_PAYLOAD))

    monkeypatch.setattr(evidence_service, "_call_vision", flaky_vision)
    result = catalogue_evidence_extraction.extract_evidence(_pdf_with_pages([None]), "x.pdf", "application/pdf")

    assert result.status == ExtractionStatus.COMPLETE
    assert sleeps == [7.0, 14.0]
    assert result.unit_outcomes[0].attempt_count == 3

    # Permanent failure: no backoff sleeps at all.
    sleeps.clear()

    def permanent_vision(_content: bytes, *, media_type: str):
        raise evidence_service._VisionExtractionFailure(
            code="EXTRACTION_CONFIGURATION_ERROR", public_message="bad config", retryable=False
        )

    monkeypatch.setattr(evidence_service, "_call_vision", permanent_vision)
    failed = catalogue_evidence_extraction.extract_evidence(_pdf_with_pages([None]), "x.pdf", "application/pdf")
    assert failed.status == ExtractionStatus.FAILED
    assert sleeps == []


def test_suspiciously_sparse_page_is_warned_not_failed(monkeypatch):
    """A valid evidence page with 1 row next to a 10-row sibling gets a
    completeness warning; the run still completes."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "configured-for-test")

    dense = {
        "page_outcome": "evidence",
        "columns": [],
        "rows": [
            {"text": f"SKU-{index} | Product {index} | HK${index}.00", "box": [0, index, 100, 10], "confidence": "0.9"}
            for index in range(1, 11)
        ],
    }
    sparse = {
        "page_outcome": "evidence",
        "columns": [],
        "rows": [{"text": "LONE-1 | Only row | HK$1.00", "box": [0, 0, 100, 10], "confidence": "0.9"}],
    }
    fake, _ = _vision_stub([dense, sparse])
    monkeypatch.setattr(evidence_service, "_call_vision", fake)

    result = catalogue_evidence_extraction.extract_evidence(
        _pdf_with_pages([None, None]), "two-pages.pdf", "application/pdf"
    )

    assert result.status == ExtractionStatus.COMPLETE
    sparse_warnings = [w for w in result.warnings if "suspiciously sparse" in w]
    assert sparse_warnings and sparse_warnings[0].startswith("page:2")


def test_a_section_banner_with_no_rows_does_not_fail_the_page(monkeypatch):
    """Emitting a table per banner means some banners have no rows on this page.

    Live: a 56-page Alfamedic run failed three times, on a different page each
    time, one of them for exactly this — a "- Genito-urinary -" heading whose
    rows continue overleaf. Refusing the page discarded every row it did have.
    """
    monkeypatch.setenv("ANTHROPIC_API_KEY", "configured-for-test")
    envelope = {
        "page_outcome": "evidence",
        "tables": [
            {"section": "- Genito-urinary -", "columns": ["Order Code", "Price"], "rows": []},
            {"section": "- Dermatology -", "columns": ["Order Code", "Price"],
             "rows": [{"cells": ["ALO250", "58.0"], "confidence": "0.95"}]},
        ],
    }
    monkeypatch.setattr(evidence_service, "_call_vision",
                        lambda content, *, media_type: evidence_service._VisionResponse(text=json.dumps(envelope)))
    content = _pdf_with_pages([""])
    result = catalogue_evidence_extraction.extract_evidence(content, "a.pdf", "application/pdf")

    assert result.status == ExtractionStatus.COMPLETE
    assert len(result.observations) == 1, "the empty banner contributes nothing and blocks nothing"
    assert result.observations[0].source_metadata["section"] == "- Dermatology -"


def test_a_page_with_no_catalogue_rows_may_still_carry_document_text(monkeypatch):
    """A page can genuinely have no product lines and still print an effective
    date. Refusing it discarded the classification AND the text."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "configured-for-test")
    envelope = {
        "page_outcome": "no_catalogue_evidence",
        "tables": [],
        "text_observations": [{"text": "Effective on 01 Mar 2026", "confidence": "0.9"}],
    }
    monkeypatch.setattr(evidence_service, "_call_vision",
                        lambda content, *, media_type: evidence_service._VisionResponse(text=json.dumps(envelope)))
    content = _pdf_with_pages([""])
    result = catalogue_evidence_extraction.extract_evidence(content, "a.pdf", "application/pdf")

    assert result.status == ExtractionStatus.COMPLETE
    assert result.unit_outcomes[0].status.value == "NO_CATALOGUE_EVIDENCE"


def test_a_page_classified_empty_that_carries_product_rows_is_still_refused(monkeypatch):
    """That is the case the rule exists for — it would hide a truncated table."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "configured-for-test")
    envelope = {
        "page_outcome": "no_catalogue_evidence",
        "tables": [{"columns": ["Order Code"], "rows": [{"cells": ["ALO250"], "confidence": "0.9"}]}],
    }
    monkeypatch.setattr(evidence_service, "_call_vision",
                        lambda content, *, media_type: evidence_service._VisionResponse(text=json.dumps(envelope)))
    content = _pdf_with_pages([""])
    result = catalogue_evidence_extraction.extract_evidence(content, "a.pdf", "application/pdf")

    assert result.status == ExtractionStatus.FAILED
    assert result.errors[0].code == "MALFORMED_PROVIDER_RESPONSE"
