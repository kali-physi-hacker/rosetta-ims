"""Conformance column matching is robust to real OCR header variance.

Regression from a live Gemini vision smoke of the Hill's price list: the vision
provider labels bilingual columns with a SPACE (not the contract's " / ") and
renders the CJK side differently from the contract text — e.g. it returns
"Gross Wholesale Price 折扣前批發價（每包／罐）" where the contract declares
"Gross Wholesale Price / 每箱·罐". Matching must tolerate both (separator
insensitivity + English-portion fallback) or every row is unconformable.
"""

from __future__ import annotations

import os
import tempfile
from uuid import uuid4

os.environ.setdefault("DATABASE_URL", f"sqlite:///{tempfile.mkdtemp()}/t.db")
os.environ.setdefault("PREFECT_API_MODE", "offline")

from services import supplier_source_contract_runtime as runtime  # noqa: E402
from services.catalogue_conformance import conform_observations  # noqa: E402
from services.catalogue_evidence_extraction import ExtractedEvidence  # noqa: E402
from schemas.catalogue_pipeline.enums import ExtractionMethod  # noqa: E402
from schemas.catalogue_pipeline.extracted_evidence_v1 import RawCell, SourceLocation  # noqa: E402
from schemas.catalogue_pipeline.supplier_contracts import get_supplier_source_contract  # noqa: E402


def _observation(cells: dict[str, str]) -> ExtractedEvidence:
    return ExtractedEvidence(
        observation_key="row-1",
        source_location=SourceLocation(row_number=1, source_object_key="row-1"),
        raw_cells=tuple(
            RawCell(cell_reference=None, row_number=1, column_index=index + 1, column_name=column, raw_value=value)
            for index, (column, value) in enumerate(cells.items())
        ),
        extraction_method=ExtractionMethod.MODEL_VISION,
        provider="test",
    )


def _observation_from_pairs(pairs: list[tuple[str, str]]) -> ExtractedEvidence:
    """A row whose headings may REPEAT — which a dict cannot express."""
    return ExtractedEvidence(
        observation_key="row-1",
        source_location=SourceLocation(row_number=1, source_object_key="row-1"),
        raw_cells=tuple(
            RawCell(cell_reference=None, row_number=1, column_index=index + 1, column_name=column, raw_value=value)
            for index, (column, value) in enumerate(pairs)
        ),
        extraction_method=ExtractionMethod.MODEL_VISION,
        provider="test",
    )


# The MOV thresholds live in a banner spanning three columns, so the vision
# model keeps them in the heading on some pages and truncates all three to an
# identical "Net Invoice Price*" on others. Both shapes must yield the same
# ladder — on the live Hill's run the truncated shape covers 59 of 238 rows.
_BASE_ROW = [
    ("Product Code 產品編號", "1141HG"),
    ("Product Range 產品系列", "Everyday Diet"),
    ("Life Stage 生命階段", "Adult 1-6"),
    ("Product Description 產品名稱", "Small Bite Lamb & Rice"),
    ("Size 重量", "3kg"),
    ("Order Multiple 訂貨單位", "1"),
    ("Gross Wholesale Price 折扣前批發價（每包／罐）", "227.2"),
]


def _tiers(fields) -> list[tuple[str, str]]:
    return [
        (str(term["condition"]["spend"]["amount"]), str(term["benefit"]["discounted_price"]["amount"]))
        for term in fields["mbb_terms"]
    ]


def test_tier_columns_are_read_left_to_right_however_they_are_labelled():
    """Every heading shape a live run has actually produced for these columns.

    Truncated is Gemini on pages 2-3; the percentage labels are Claude on
    pages 1-2 of the same document; the MOV form is both providers elsewhere.
    A ladder must come out the same from all of them.
    """
    hills = runtime.load_contract(14)
    for labels in (
        ["Net Invoice Price* 折扣批發價*"] * 3,
        ["Net Invoice Price* 折扣批發價* (0%)",
         "Net Invoice Price* 折扣批發價* (4%)",
         "Net Invoice Price* 折扣批發價* (6%)"],
        ["Net Invoice Price* 折扣批發價* (MOV $1,200)",
         "Net Invoice Price* 折扣批發價* (MOV $2,200)",
         "Net Invoice Price* 折扣批發價* (MOV $4,500)"],
    ):
        row = _BASE_ROW + list(zip(labels, ["215.8", "206.8", "202.2"]))
        outcome = conform_observations((_observation_from_pairs(row),), (uuid4(),), hills)
        assert _tiers(outcome.items[0].normalized_fields) == [
            ("1200", "215.8"), ("2200", "206.8"), ("4500", "202.2")
        ], f"failed for {labels[0]!r}"


def test_repeated_tier_headings_are_read_left_to_right():
    """Page 3 of the live catalogue: three columns, one indistinguishable heading."""
    hills = runtime.load_contract(14)
    row = _BASE_ROW + [
        ("Net Invoice Price* 折扣批發價*", "215.8"),
        ("Net Invoice Price* 折扣批發價*", "206.8"),
        ("Net Invoice Price* 折扣批發價*", "202.2"),
    ]
    outcome = conform_observations((_observation_from_pairs(row),), (uuid4(),), hills)

    assert _tiers(outcome.items[0].normalized_fields) == [
        ("1200", "215.8"), ("2200", "206.8"), ("4500", "202.2")
    ], "leftmost column is the lowest threshold, as the document prints them"


def test_the_named_thresholds_still_win_where_the_model_kept_them():
    """Page 1: the headings name their own tier. Position must not override that."""
    hills = runtime.load_contract(14)
    row = _BASE_ROW + [
        ("Net Invoice Price* 折扣批發價* 購貨金額滿 MOV $1,200", "151.8"),
        ("Net Invoice Price* 折扣批發價* 購貨金額滿 MOV $2,200", "145.4"),
        ("Net Invoice Price* 折扣批發價* 購貨金額滿 MOV $4,500", "142.2"),
    ]
    outcome = conform_observations((_observation_from_pairs(row),), (uuid4(),), hills)

    assert _tiers(outcome.items[0].normalized_fields) == [
        ("1200", "151.8"), ("2200", "145.4"), ("4500", "142.2")
    ]


def test_a_column_outside_the_family_is_never_read_as_a_tier():
    """Prefix matching must not swallow the gross price sitting next to them."""
    hills = runtime.load_contract(14)
    row = _BASE_ROW + [("Net Invoice Price* 折扣批發價* (0%)", "215.8")]
    outcome = conform_observations((_observation_from_pairs(row),), (uuid4(),), hills)
    fields = outcome.items[0].normalized_fields
    assert _tiers(fields) == [("1200", "215.8")]
    assert fields["cost"]["amount"] == "227.2", "the gross wholesale column is untouched"


def test_a_single_tier_column_does_not_become_three_identical_terms():
    """The flat-ladder bug: one column answering every tier is not a discount."""
    hills = runtime.load_contract(14)
    row = _BASE_ROW + [("Net Invoice Price* 折扣批發價*", "215.8")]
    outcome = conform_observations((_observation_from_pairs(row),), (uuid4(),), hills)

    assert _tiers(outcome.items[0].normalized_fields) == [("1200", "215.8")]


def test_rows_without_any_tier_column_carry_no_terms():
    """Pages 4-9 are prescription diets — the catalogue prints no ladder for them."""
    hills = runtime.load_contract(14)
    outcome = conform_observations((_observation_from_pairs(_BASE_ROW),), (uuid4(),), hills)

    assert outcome.items[0].normalized_fields["mbb_terms"] == []


def test_real_gemini_bilingual_headers_map_through_the_contract():
    hills = runtime.load_contract(14)
    # Column labels EXACTLY as gemini-flash returned them for the Hill's page:
    # space separators, and a cost column whose CJK diverges from the contract.
    gemini_row = {
        "Product Code 產品編號": "10447",
        "Product Range 產品系列": "Science Plan",
        "Life Stage 生命階段": "Adult",
        "Product Description 產品名稱": "Chicken 82g",
        "Size 重量": "82g",
        "Gross Wholesale Price 折扣前批發價（每包／罐）": "13.10",
        "Order Multiple 訂貨單位": "12",
        # Extra columns Gemini emits that the contract does not declare — ignored.
        "Regular Retail Price 正價": "19.00",
    }
    outcome = conform_observations((_observation(gemini_row),), (uuid4(),), hills)

    assert len(outcome.items) == 1
    fields = outcome.items[0].normalized_fields
    assert fields["supplier_sku"]["value"] == "10447"
    assert fields["product_name"]["value"] == "Science Plan Adult Chicken 82g"
    assert fields["brand"]["value"] == "Hill's"  # contract constant
    # Cost mapped despite the CJK side differing from the contract text.
    assert fields["cost"]["amount"] == "13.10"
    assert fields["cost"]["currency"] == "HKD"


def test_header_row_of_gemini_labels_is_skipped():
    hills = runtime.load_contract(14)
    header = {c: c for c in (
        "Product Code 產品編號", "Product Range 產品系列", "Life Stage 生命階段",
        "Product Description 產品名稱", "Size 重量", "Gross Wholesale Price 折扣前批發價",
        "Order Multiple 訂貨單位",
    )}
    outcome = conform_observations((_observation(header),), (uuid4(),), hills)
    assert outcome.items == ()
    assert outcome.skipped_count == 1


def test_bilingual_cell_values_compose_a_clean_english_product_name():
    hills = runtime.load_contract(14)
    # Bilingual VALUES exactly as Gemini vision returned them for a Hill's row.
    gemini_row = {
        "Product Code 產品編號": "10445",
        "Product Range 產品系列": "健康燉肉 Healthy Cuisine",
        "Life Stage 生命階段": "幼貓 Kitten",
        "Product Description 產品名稱": "健康燉肉配方 Healthy Cuisine",
        "Size 重量": "82g",
        "Gross Wholesale Price 折扣前批發價（每包／罐）": "13.10",
    }
    row = conform_observations((_observation(gemini_row),), (uuid4(),), hills).items[0]
    # raw_fields keeps the verbatim bilingual join (the contract's composed_from
    # order: range, life stage, description). normalized is the same join with the
    # CJK removed — no recomposition beyond the contract (no brand/size, no dedup).
    assert row.raw_fields["product_name"] == "健康燉肉 Healthy Cuisine 幼貓 Kitten 健康燉肉配方 Healthy Cuisine"
    assert row.normalized_fields["product_name"]["value"] == "Healthy Cuisine Kitten Healthy Cuisine"


def test_contract_aliases_are_executable_and_declared_values_are_preserved():
    declaration = get_supplier_source_contract("kangaroo.mixed_price_catalogue.v1", "v1").declaration
    contract = runtime.SupplierSourceRuntimeContract(declaration=declaration)
    alias_row = {
        "SKU#": "KPN-10",
        "Product Description": "Duck bites",
        "Size": "100g",
        "Price Per Unit": "42.50",
        "Retail Price Per Unit": "55.00",
        "section_header": "2026-07-01",
        "section_notes": "Buy 10 get 1 free",
    }

    outcome = conform_observations((_observation(alias_row),), (uuid4(),), contract)
    row = outcome.items[0]

    assert not [issue for issue in outcome.issues if issue.issue_code == "CONTRACT_REQUIRED_HEADER_MISSING"]
    assert row.raw_fields["supplier_sku"] == "KPN-10"
    assert row.raw_fields["rrp"] == "55.00"
    assert row.raw_fields["mbb_text"] == "Buy 10 get 1 free"
    assert row.raw_fields["effective_date"] == "2026-07-01"
    assert row.raw_fields["additional_fields"]["supplier_sku"] == "KPN-10"
    assert row.raw_fields["additional_fields"]["cost"] == "42.50"
    assert row.normalized_fields["rrp"]["amount"] == "55.00"
    assert row.normalized_fields["effective_date"]["value"] == "2026-07-01"
    assert row.normalized_fields["mbb_terms"] == []
    assert "CONTRACT_MBB_REQUIRES_REVIEW" in {issue.issue_code for issue in row.issues}


def test_missing_required_row_field_is_explicit_and_blocking():
    hills = runtime.load_contract(14)
    incomplete_row = {
        "Product Code 產品編號": "10447",
        "Product Range 產品系列": "Science Plan",
        "Life Stage 生命階段": "Adult",
        "Product Description 產品名稱": "Chicken 82g",
        "Size 重量": "82g",
        # Required wholesale price is missing.
        "Order Multiple 訂貨單位": "12",
    }

    row = conform_observations((_observation(incomplete_row),), (uuid4(),), hills).items[0]

    missing = {issue.field_key for issue in row.issues if issue.issue_code == "CONTRACT_REQUIRED_FIELD_MISSING"}
    assert missing == {"cost"}
    assert any(issue.severity == "BLOCKING" for issue in row.issues)


def test_missing_required_document_header_is_not_silently_accepted():
    hills = runtime.load_contract(14)
    incomplete_shape = {
        "Product Code 產品編號": "10447",
        "Product Range 產品系列": "Science Plan",
        "Product Description 產品名稱": "Chicken 82g",
    }

    outcome = conform_observations((_observation(incomplete_shape),), (uuid4(),), hills)

    issue_codes = {issue.issue_code for issue in outcome.issues}
    assert "CONTRACT_REQUIRED_HEADER_MISSING" in issue_codes
    assert outcome.metadata["degraded"] is True


def test_declared_validation_rule_runs_in_the_authoritative_conformance_path():
    hills = runtime.load_contract(14)
    invalid_price_row = {
        "Product Code 產品編號": "10447",
        "Product Range 產品系列": "Science Plan",
        "Life Stage 生命階段": "Adult",
        "Product Description 產品名稱": "Chicken 82g",
        "Size 重量": "82g",
        "Gross Wholesale Price 折扣前批發價（每包／罐）": "25.00",
        "Recommended Retail Selling Price 建議零售價": "20.00",
        "Order Multiple 訂貨單位": "12",
    }

    row = conform_observations((_observation(invalid_price_row),), (uuid4(),), hills).items[0]

    assert "HILLS_COST_NOT_BELOW_RRP" in {issue.issue_code for issue in row.issues}


def test_hills_packaging_normalization_keeps_content_separate_from_ordering():
    hills = runtime.load_contract(14)
    row_cells = {
        "Product Code 產品編號": "607665",
        "Product Range 產品系列": "Cancer",
        "Life Stage 生命階段": "ONC",
        "Product Description 產品名稱": "Chicken Stew",
        "Size 重量": "24/2.9 oz",
        "Gross Wholesale Price 折扣前批發價（每包／罐）": "25.20",
        "Order Multiple 訂貨單位": "24",
    }

    row = conform_observations((_observation(row_cells),), (uuid4(),), hills).items[0]
    packaging = row.normalized_fields["packaging"]

    assert packaging["price_basis"]["code"] == "UNIT"
    assert packaging["content_amount"] == "2.9"
    assert packaging["content_uom"]["code"] == "OZ"
    assert packaging["order_increment"] == {"amount": "24", "uom": {"code": "UNIT", "label": None}}
    assert "sellable_units_per_purchase_unit" not in packaging
    assert "purchase_uom" not in packaging
    assert "break_pack_allowed" not in packaging


def test_alfamedic_pack_count_is_order_increment_and_by_quote_is_reviewed():
    alfamedic = runtime.load_contract(1)
    row_cells = {
        "Order Code": "MS-8",
        "Product Name": "Image Processor",
        "Brand": "Skyla",
        "Packing / Unit": "10 pcs/ box",
        "Price/ Unit (HKD)": "By Quote",
    }

    row = conform_observations((_observation(row_cells),), (uuid4(),), alfamedic).items[0]
    packaging = row.normalized_fields["packaging"]

    assert "cost" not in row.normalized_fields
    assert packaging["price_basis"]["code"] == "PIECE"
    assert packaging["order_increment"]["amount"] == "10"
    assert "sellable_units_per_purchase_unit" not in packaging
    assert "CONTRACT_NULL_COST_REQUIRES_REVIEW" in {issue.issue_code for issue in row.issues}


def test_unparseable_effective_date_stays_raw_and_requires_review():
    declaration = get_supplier_source_contract("kangaroo.mixed_price_catalogue.v1", "v1").declaration
    contract = runtime.SupplierSourceRuntimeContract(declaration=declaration)
    row_cells = {
        "SKU#": "KPN-11",
        "Product Description": "Duck bites",
        "Price Per Unit": "42.50",
        "Retail Price Per Unit": "55.00",
        "section_header": "effective next promotion",
    }

    row = conform_observations((_observation(row_cells),), (uuid4(),), contract).items[0]

    assert row.raw_fields["effective_date"] == "effective next promotion"
    assert "effective_date" not in row.normalized_fields
    assert "CONTRACT_EFFECTIVE_DATE_UNPARSEABLE" in {issue.issue_code for issue in row.issues}


def test_unresolved_price_basis_never_produces_a_cost_proposal():
    declaration = get_supplier_source_contract("kangaroo.earthz_pet_price_sheet.v1", "v1").declaration
    contract = runtime.SupplierSourceRuntimeContract(declaration=declaration)
    row_cells = {
        "sku#": "EARTHZ-35",
        "visual product heading": "Earthz supplement",
        "visual size and pack-count text": "5 bottles x 35ml",
        "批發價": "100.00",
        "建議零售價": "130.00",
    }

    row = conform_observations((_observation(row_cells),), (uuid4(),), contract).items[0]

    assert row.raw_fields["cost"] == "100.00"
    assert "cost" not in row.normalized_fields
    assert row.normalized_fields["packaging"]["content_amount"] == "35"
    assert row.normalized_fields["packaging"]["content_uom"]["code"] == "ML"
    assert "CONTRACT_PRICE_BASIS_UNRESOLVED" in {issue.issue_code for issue in row.issues}


def test_hills_contract_conforms_both_science_diet_and_prescription_diet_editions():
    """One Hill's contract, two live document families.

    Labels exactly as Gemini vision returned them on the real files: the
    classic edition prints Life Stage; the 2026 Prescription Diet edition
    replaces it with Disease Category. Both must satisfy required headers and
    compose a product name from whichever dimension columns are present.
    """
    hills = runtime.load_contract(14)

    classic = {
        "Product Code 產品編號": "10447",
        "Product Range 產品系列": "健康燉肉 Healthy Cuisine",
        "Life Stage 生命階段": "幼貓 Kitten",
        "Product Description 產品名稱": "健康燉肉配方 Healthy Cuisine",
        "Size 重量": "82g",
        "Gross Wholesale Price 折扣前批發價（每包／罐）": "13.10",
        "Order Multiple 訂貨單位": "12",
    }
    prescription = {
        "Product Code 產品編號": "607665",
        "Disease Category 疾病種類": "Cancer",
        "Product Range 產品系列": "ONC",
        "Product Description 產品名稱": "ONC Care Chicken Stew - Cancer Care",
        "Size 重量": "2.9 oz",
        "Gross Wholesale Price 折扣前批發價（每包／罐）": "25.20",
        "Order Multiple 訂貨單位": "24",
    }

    for label, row, expected_name in (
        ("classic", classic, "Healthy Cuisine Kitten Healthy Cuisine"),
        ("prescription", prescription, "ONC Cancer ONC Care Chicken Stew - Cancer Care"),
    ):
        outcome = conform_observations((_observation(row),), (uuid4(),), hills)
        item = outcome.items[0]
        header_issues = [
            issue.issue_code
            for issue in item.issues
            if issue.issue_code in ("CONTRACT_REQUIRED_HEADER_MISSING", "CONTRACT_REQUIRED_FIELD_MISSING")
        ]
        assert header_issues == [], f"{label}: {header_issues}"
        assert item.normalized_fields["product_name"]["value"] == expected_name, label
        assert item.normalized_fields["supplier_sku"]["value"] == row["Product Code 產品編號"], label
        assert item.normalized_fields["cost"]["currency"] == "HKD", label


def test_text_only_lines_under_tabular_contract_are_furniture_not_blocking(monkeypatch):
    """Real flash+compact behaviour: page banners/titles/footnotes arrive as
    text-only observations. Under a TABULAR contract they are skipped like
    header rows (evidence preserved upstream) — NOT 48 blocking issues."""
    from services import catalogue_conformance
    from services.catalogue_evidence_extraction import ExtractedEvidence as _EE

    hills = runtime.load_contract(14)
    banner = _EE(
        observation_key="furniture-1",
        source_location=SourceLocation(page_number=1, source_object_key="furniture-1"),
        raw_text="濕糧罐頭 Wet Food",
        extraction_method=ExtractionMethod.MODEL_VISION,
        provider="test",
    )
    product = _observation({
        "Product Code 產品編號": "10447",
        "Product Range 產品系列": "Science Plan",
        "Product Description 產品名稱": "Chicken 82g",
        "Size 重量": "82g",
        "Gross Wholesale Price 折扣前批發價": "13.10",
        "Order Multiple 訂貨單位": "12",
    })
    outcome = conform_observations((banner, product), (uuid4(), uuid4()), hills)

    assert len(outcome.items) == 1  # only the product row normalized
    assert outcome.skipped_count == 1
    assert outcome.metadata["skipped_non_tabular_text"] == 1
    assert outcome.metadata["unconformable_items"] == 0
    assert not any(issue.issue_code == "CONTRACT_ROW_UNCONFORMABLE" for item in outcome.items for issue in item.issues)

    # A NON-tabular contract keeps the manual-review path for text lines.
    monkeypatch.setattr(catalogue_conformance, "_contract_is_tabular", lambda _c: False)
    reviewed = conform_observations((banner,), (uuid4(),), hills)
    assert reviewed.metadata["unconformable_items"] == 1
    assert any(issue.issue_code == "CONTRACT_ROW_UNCONFORMABLE" for item in reviewed.items for issue in item.issues)


# ── A price list is not only items: it has section titles, blank spacers and
#    continuation lines, and a vision model returns them as table rows because
#    that is what they look like. On one live Alfamedic run 38 of 52 blocked
#    rows were furniture — which is how a review queue stops being read. ─────

def _alfamedic_row(pairs):
    return _observation_from_pairs(pairs)


def test_a_section_divider_is_skipped_rather_than_queued_for_review():
    """Nobody should be asked to adjudicate the words "Dry Food"."""
    alfamedic = runtime.load_contract(1)
    outcome = conform_observations(
        (_alfamedic_row([("Product Name", "- Diagnostics, Lab Equipment & Accessories -")]),),
        (uuid4(),),
        alfamedic,
    )

    assert outcome.items == (), "furniture is evidence, not a catalogue row"
    assert outcome.metadata["skipped_ineligible_rows"] == 1, "and it is counted, not silently dropped"


def test_a_row_missing_its_code_but_carrying_a_price_is_still_surfaced():
    """The protective case: that is an item we failed to read, not furniture.

    Seen for real — a live Gemini run left four rows with an order code and no
    price, and those must reach a human rather than vanish into a skip count.
    """
    alfamedic = runtime.load_contract(1)
    outcome = conform_observations(
        (_alfamedic_row([("Product Name", "Bioguard CDV Ag Test"), ("Price/ Unit (HKD)", "190.0")]),),
        (uuid4(),),
        alfamedic,
    )

    assert len(outcome.items) == 1
    assert outcome.metadata["skipped_ineligible_rows"] == 0
    assert any(i.issue_code == "CONTRACT_REQUIRED_FIELD_MISSING" and i.field_key == "supplier_sku"
               for i in outcome.items[0].issues)


def test_a_service_without_a_pack_size_is_a_normal_catalogue_row():
    """A lab test is priced per test and prints no packing.

    Requiring one blocked 13 real priced items on a live run for lacking a
    field their own supplier does not print.
    """
    alfamedic = runtime.load_contract(1)
    outcome = conform_observations(
        (_alfamedic_row([
            ("Order Code", "SPOT1"),
            ("Product Name", "Spot Platinum+ Test for Canine/ Feline/ Equine only"),
            ("Price/ Unit (HKD)", "2,250.0"),
        ]),),
        (uuid4(),),
        alfamedic,
    )

    assert len(outcome.items) == 1
    # Document-level "header not observed" issues are expected on a one-row
    # fixture; what must be gone is the ROW being blocked for its own packing.
    assert not [i for i in outcome.items[0].issues
                if i.severity == "BLOCKING" and i.field_key == "pack_size"]
    # The thousands separator is not a reason to lose a price either.
    assert outcome.items[0].normalized_fields["cost"]["amount"] == "2250.0"


def test_hills_section_headings_are_furniture_too():
    hills = runtime.load_contract(14)
    outcome = conform_observations(
        (_observation_from_pairs([("Product Description 產品名稱", "小食 Treats")]),),
        (uuid4(),),
        hills,
    )

    assert outcome.items == ()
    assert outcome.metadata["skipped_ineligible_rows"] == 1


def test_a_contract_without_declared_identity_fields_keeps_every_row():
    """The rule is opt-in per contract; silence must not start dropping rows."""
    from types import SimpleNamespace

    from services.catalogue_conformance import _is_ineligible_row

    def contract(identity_fields):
        return SimpleNamespace(declaration=SimpleNamespace(
            source_structure=SimpleNamespace(row_identity_fields=identity_fields),
            pricing=SimpleNamespace(cost_source_field="cost"),
        ))

    bare = {"source:description": "小食 Treats"}
    assert _is_ineligible_row(bare, contract(["supplier_sku"])) is True
    assert _is_ineligible_row(bare, contract([])) is False, "no declaration, no skipping"
    # And identity or price is enough to keep a row either way.
    assert _is_ineligible_row({"source:supplier_sku": "SPOT1"}, contract(["supplier_sku"])) is False
    assert _is_ineligible_row({"source:cost": "190.0"}, contract(["supplier_sku"])) is False


def test_every_observation_is_either_an_item_or_counted_as_skipped():
    """The run's row accounting rests on this, and nothing else enforces it.

    Adding a new reason to skip a row without adding it to skipped_count made a
    56-page live run report 2,233 extracted minus 309 skipped against 1,762
    persisted — 162 rows silently unaccounted for. Cheap to assert here;
    expensive to notice there.
    """
    alfamedic = runtime.load_contract(1)
    observations = (
        _alfamedic_row([("Order Code", "BGD-RC03-1"), ("Product Name", "CDV Ag Test"),
                        ("Price/ Unit (HKD)", "190.0"), ("Packing / Unit", "10 tests kit/ box")]),
        _alfamedic_row([("Product Name", "- Diagnostics, Lab Equipment & Accessories -")]),   # ineligible
        _alfamedic_row([("Order Code", "Order Code"), ("Product Name", "Product Name"),
                        ("Price/ Unit (HKD)", "Price/ Unit (HKD)")]),                          # header
        _alfamedic_row([("Order Code", "SPOT1"), ("Product Name", "Spot Platinum+ Test"),
                        ("Price/ Unit (HKD)", "2,250.0")]),
    )
    outcome = conform_observations(observations, tuple(uuid4() for _ in observations), alfamedic)

    assert len(outcome.items) + outcome.skipped_count == len(observations)
    # And the reasons are reported separately, each true on its own: they had
    # all been reporting the running total, so 0 header rows read as 471.
    meta = outcome.metadata
    assert meta["skipped_ineligible_rows"] == 1
    assert meta["skipped_header_rows"] == 1
    assert (meta["skipped_header_rows"] + meta["skipped_ineligible_rows"]
            + meta["skipped_non_tabular_text"]) == outcome.skipped_count


# ── Size variants share one printed name cell. They are separate SKUs and
#    have to be stocked as such. Live page 20 of the Alfamedic catalogue:
#
#      ALO250   ALOVEEN Shampoo   250ml/bot  58.0
#      (none)   —                 —          56.0   <- quantity tier
#      ALO1000  —                 1L/bot     190.0  <- variant, own SKU
# ──────────────────────────────────────────────────────────────────────────

def _alf_rows(rows, page=20):
    out = []
    for index, pairs in enumerate(rows):
        cells = tuple(
            RawCell(cell_reference=None, row_number=index + 1, column_index=i + 1,
                    column_name=col, raw_value=val)
            for i, (col, val) in enumerate(pairs)
        )
        out.append(ExtractedEvidence(
            observation_key=f"page:{page}:row:{index}",
            source_location=SourceLocation(page_number=page, row_number=index + 1,
                                           source_object_key=f"page:{page}:row:{index}"),
            raw_cells=cells,
            extraction_method=ExtractionMethod.MODEL_VISION,
            provider="test",
        ))
    return tuple(out)


def test_a_size_variant_takes_the_name_it_is_printed_under():
    alfamedic = runtime.load_contract(1)
    observations = _alf_rows([
        [("Order Code", "ALO250"), ("Product Name", "ALOVEEN Shampoo"),
         ("Packing/ Unit", "250ml/ bot"), ("Price/ Unit (HKD)", "58.0")],
        [("Order Code", "ALO1000"), ("Packing/ Unit", "1L/ bot"), ("Price/ Unit (HKD)", "190.0")],
    ])
    out = conform_observations(observations, tuple(uuid4() for _ in observations), alfamedic)

    assert len(out.items) == 2
    variant = out.items[1]
    assert variant.normalized_fields["product_name"]["value"] == "ALOVEEN Shampoo"
    assert variant.normalized_fields["supplier_sku"]["value"] == "ALO1000", "its own SKU, not merged away"
    assert variant.provenance["inherited_from_row_above"] == ["description"], "and it says so"
    assert "inherited_from_row_above" not in out.items[0].provenance


def test_a_quantity_tier_line_never_acquires_a_name():
    """A priced line with no code is a tier, not a product. Naming it would
    invent a SKU that does not exist."""
    alfamedic = runtime.load_contract(1)
    observations = _alf_rows([
        [("Order Code", "ALO250"), ("Product Name", "ALOVEEN Shampoo"),
         ("Packing/ Unit", "250ml/ bot"), ("Price/ Unit (HKD)", "58.0")],
        [("Price/ Unit (HKD)", "56.0")],
    ])
    out = conform_observations(observations, tuple(uuid4() for _ in observations), alfamedic)

    tier = next(i for i in out.items if not (i.normalized_fields.get("supplier_sku") or {}).get("value"))
    assert "product_name" not in tier.normalized_fields
    assert not tier.provenance.get("inherited_from_row_above")


def test_a_name_does_not_carry_across_a_page_break():
    """The last product on page 20 has nothing to do with the first on page 21."""
    alfamedic = runtime.load_contract(1)
    observations = _alf_rows([
        [("Order Code", "ALO250"), ("Product Name", "ALOVEEN Shampoo"), ("Price/ Unit (HKD)", "58.0")],
    ]) + _alf_rows([
        [("Order Code", "XYZ999"), ("Packing/ Unit", "1L/ bot"), ("Price/ Unit (HKD)", "190.0")],
    ], page=21)
    out = conform_observations(observations, tuple(uuid4() for _ in observations), alfamedic)

    assert "product_name" not in out.items[1].normalized_fields


def test_the_plainly_headed_price_column_is_still_a_price():
    """Alfamedic's diagnostics and suture tables head the column "Price".

    Same meaning, narrower heading — those tables carry no per-unit qualifier.
    146 rows of one live 56-page catalogue priced that way and every one was
    read as unpriced: 900-100 Pre-anesthetic Panel prints 1,760.0 and reached
    review as having no price at all.
    """
    alfamedic = runtime.load_contract(1)
    observations = _alf_rows([[
        ("Order Code", "900-100"),
        ("Product Name", "Pre-anesthetic Panel (7 assay +3)"),
        ("Sample Type", "Whole Blood/ Plasma/ Serum"),
        ("Volume", "220μL"),
        ("Brand", "Skyla"),
        ("Packing/ Unit", "20 pcs/ box"),
        ("Order Unit", "1 box"),
        ("Price", "1,760.0"),
    ]], page=10)
    out = conform_observations(observations, tuple(uuid4() for _ in observations), alfamedic)

    assert out.items[0].normalized_fields["cost"]["amount"] == "1760.0"
    assert out.items[0].normalized_fields["cost"]["currency"] == "HKD"


def test_the_qualified_price_column_still_wins_where_both_appear():
    """"Price/ Unit (HKD)" is the declared column; the bare alias is a fallback."""
    alfamedic = runtime.load_contract(1)
    observations = _alf_rows([[
        ("Order Code", "ALO250"),
        ("Product Name", "ALOVEEN Shampoo"),
        ("Price/ Unit (HKD)", "58.0"),
        ("Price", "999.0"),
    ]])
    out = conform_observations(observations, tuple(uuid4() for _ in observations), alfamedic)

    assert out.items[0].normalized_fields["cost"]["amount"] == "58.0"
