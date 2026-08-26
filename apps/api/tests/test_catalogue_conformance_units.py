"""Focused unit tests for the conformance behaviours added during the
KPN Trading / Kangaroo Pet Nutrition / Vetapet contract-verification effort.

Each behaviour below was first proven against real per-page evidence with the
out-of-repo harness (simulate_conformance.py over KPN_Kangaroo.evidence /
Vetapet.evidence) — but that harness is not CI. These tests pin the behaviours
with the same values the real catalogues print, so a regression fails loudly
here instead of silently corrupting a future ingestion run.
"""

from __future__ import annotations

import os
import tempfile
from decimal import Decimal

import pytest
from uuid import uuid4

os.environ.setdefault("DATABASE_URL", f"sqlite:///{tempfile.mkdtemp()}/t.db")
os.environ.setdefault("PREFECT_API_MODE", "offline")

from services import supplier_source_contract_runtime as runtime  # noqa: E402
from services.catalogue_conformance import (  # noqa: E402
    _column_keys,
    _decimal_value,
    _english_text,
    _identity_names_overlap,
    _money_amounts,
    _purchase_unit_from_text,
    conform_observations,
)
from services.catalogue_evidence_extraction import ExtractedEvidence  # noqa: E402
from schemas.catalogue_pipeline.enums import ExtractionMethod  # noqa: E402
from schemas.catalogue_pipeline.extracted_evidence_v1 import RawCell, SourceLocation  # noqa: E402
from schemas.catalogue_pipeline.supplier_contracts import get_supplier_source_contract  # noqa: E402


def _observation(cells: dict[str, str], *, metadata: dict | None = None, key: str = "row-1") -> ExtractedEvidence:
    return ExtractedEvidence(
        observation_key=key,
        source_location=SourceLocation(row_number=1, source_object_key=key),
        raw_cells=tuple(
            RawCell(cell_reference=None, row_number=1, column_index=index + 1, column_name=column, raw_value=value)
            for index, (column, value) in enumerate(cells.items())
        ),
        extraction_method=ExtractionMethod.MODEL_VISION,
        provider="test",
        source_metadata=metadata or {},
    )


def _conform_one(contract_id: str, observation: ExtractedEvidence):
    registration = get_supplier_source_contract(contract_id, "v1")
    contract = runtime.SupplierSourceRuntimeContract(declaration=registration.declaration)
    outcome = conform_observations((observation,), (uuid4(),), contract)
    assert len(outcome.items) == 1
    return outcome.items[0]


# ─────────────────────────────────────────────────────────────────────────
# Money parsing — every printed form the three catalogues actually use.
# ─────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("printed", "expected"),
    [
        ("HK$113", Decimal("113")),            # HK$ must strip before bare $
        ("HKD$120", Decimal("120")),           # Vetapet retail prefix form
        ("$1,094", Decimal("1094")),           # thousands separator
        ("HK$219.0/pcs", Decimal("219.0")),    # unit suffix names the basis
        ("HK$14.00/罐", Decimal("14.00")),      # CJK unit suffix
        ("批發價$392", Decimal("392")),          # column heading leaked into the cell
        ("$340\n(@28.3)", Decimal("340")),     # printed derived per-can average
        ("590\n(@24.6)", Decimal("590")),      # same, without a currency mark
        ("130.0 (Price Reduced)", Decimal("130.0")),  # Alfamedic remark note (from main) — digit-free notes drop
    ],
)
def test_printed_price_forms_parse(printed, expected):
    assert _decimal_value(printed) == expected


@pytest.mark.parametrize(
    "printed",
    [
        "By Quote",            # explicit null-cost marker
        "3/8",                 # a fraction is not a suffixed price
        "$739 HK$1056.0",      # struck price beside an offer price — never guess
        "$1094/箱\n($182/包)",  # two UNMARKED amounts — refused, unlike (@N)
    ],
)
def test_ambiguous_or_null_price_forms_refuse(printed):
    assert _decimal_value(printed) is None


def test_money_amounts_reads_every_printed_amount_in_a_compound_cell():
    assert _money_amounts("$1094/箱\n($182/包)") == [Decimal("1094"), Decimal("182")]
    assert _money_amounts("HK$324") == [Decimal("324")]


# ─────────────────────────────────────────────────────────────────────────
# Column-heading matching.
# ─────────────────────────────────────────────────────────────────────────


def test_letter_spaced_cjk_headings_match_compact_declarations():
    """Vetapet's retail section letter-spaces its printed headings."""
    assert set(_column_keys("批 發 價")) & set(_column_keys("批發價"))
    assert set(_column_keys("編 號")) & set(_column_keys("編號"))


def test_currency_only_english_folds_are_not_match_keys():
    """'批發價 (HKD)' and '建議零售價 (HKD) 每罐' both English-fold to 'hkd';
    matching on it silently filled COST from the RRP column on the real
    Kangaroo wet-can page."""
    assert "hkd" not in _column_keys("批發價 (HKD)")
    assert "hkd" not in _column_keys("建議零售價 (HKD) 每罐")
    # Real English names keep their fold.
    assert "product description" in _column_keys("產品內容 Product Description")


# ─────────────────────────────────────────────────────────────────────────
# Text helpers.
# ─────────────────────────────────────────────────────────────────────────


def test_english_text_keeps_latin_and_refuses_punctuation_residue():
    # Bilingual: the Latin portion survives; empty parens where the CJK sat are
    # a known cosmetic artifact (the curly apostrophe is non-ASCII and drops).
    assert _english_text("Stella’s Super Beef\n牛魔王\n(牛肉配方)") == "Stella s Super Beef ( )"
    # A pure-Chinese name leaves ONLY '( )' after ASCII-stripping — residue,
    # not a name; None lets the caller's verbatim fallback win.
    assert _english_text("無穀幼犬糧 (新包裝)") is None


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("30ml/ bot", "BOTTLE"),   # Alfamedic slash form
        ("1 box", "BOX"),          # Vetapet ORDER UNIT bare form
        ("12 pcs", "PIECE"),
        ("1 set", None),           # deliberately absent from the vocabulary
        ("30ml", None),            # a measure is not a purchase unit
    ],
)
def test_purchase_unit_reads_slash_and_bare_forms(text, expected):
    assert _purchase_unit_from_text(text) == expected


def test_identity_matching_tolerates_punctuation_but_not_other_suppliers():
    # The printed footer omits the second period: 'K.P.N Trading Ltd.'
    assert _identity_names_overlap("K.P.N Trading Ltd.", "K.P.N. Trading")
    assert _identity_names_overlap("袋鼠寵物營養有限公司 Kangaroo Pet Nutrition Ltd.", "Kangaroo Pet Nutrition")
    assert not _identity_names_overlap("港澳總代理 袋鼠寵物營養有限公司 Kangaroo Pet Nutrition Ltd.", "K.P.N. Trading")


# ─────────────────────────────────────────────────────────────────────────
# Contract-level behaviours, through the real registered contracts.
# ─────────────────────────────────────────────────────────────────────────

_FRB_ROW = {
    "產品編號": "FRB-3",
    "產品名稱": "Stella's Super Beef 牛魔王",
    "原箱包數": "6包",
    "批發價 每包": "$204",
    "批發價 每箱 (平均每包價)": "$1094/箱\n($182/包)",
    "建議零售價 每包": "$276",
}


def test_supplier_identity_mismatch_fires_only_on_contrary_evidence():
    contract_id = "kpn_trading.pack_price_list.v1"
    wrong = _conform_one(
        contract_id,
        _observation(_FRB_ROW, metadata={"supplier_identity_text": "Kangaroo Pet Nutrition Ltd."}),
    )
    assert any(i.issue_code == "CONTRACT_SUPPLIER_IDENTITY_MISMATCH" for i in wrong.issues)

    right = _conform_one(
        contract_id,
        _observation(_FRB_ROW, metadata={"supplier_identity_text": "凱邦商貿有限公司 K.P.N Trading Ltd."}),
    )
    assert not any(i.issue_code == "CONTRACT_SUPPLIER_IDENTITY_MISMATCH" for i in right.issues)

    silent = _conform_one(contract_id, _observation(_FRB_ROW))
    assert not any(i.issue_code == "CONTRACT_SUPPLIER_IDENTITY_MISMATCH" for i in silent.issues)


def test_quantity_conditioned_case_tier_emits_structured_term():
    """FRB-3: buy 6+ packs -> pay the printed per-pack average of 182."""
    item = _conform_one("kpn_trading.pack_price_list.v1", _observation(_FRB_ROW))
    assert item.normalized_fields["cost"]["amount"] == "204"
    assert item.normalized_fields["cost"]["price_basis"]["code"] == "PACK"
    (term,) = item.normalized_fields["mbb_terms"]
    assert term["scope"] == "SUPPLIER_SKU"
    assert term["condition"]["condition_type"] == "minimum_quantity"
    assert term["condition"]["quantity"]["amount"] == "6"
    assert term["benefit"]["discounted_price"]["amount"] == "182"


def test_case_total_without_printed_unit_rate_emits_no_term():
    """A case column carrying only a bundle total must not be divided into a
    per-unit rate the source never printed."""
    row = dict(_FRB_ROW)
    row["批發價 每箱 (平均每包價)"] = "$1094"
    item = _conform_one("kpn_trading.pack_price_list.v1", _observation(row))
    assert item.normalized_fields["mbb_terms"] == []


def test_unlabeled_column_resolves_only_when_exactly_one_value_exists():
    """The NOW FRESH single-price layout prints the product NAME under an
    unlabeled column; the sentinel resolves it only when the row carries
    exactly one non-empty unlabeled value."""
    contract_id = "kpn_trading.pack_price_list.v1"
    row = {
        "": "Adult Dog 成犬 雞肉配方 Cage-Free Chicken",
        "sku#": "FG00610",
        "包裝": "3.5lb",
        "批發價": "$215",
        "零售價": "$310",
    }
    item = _conform_one(contract_id, _observation(row))
    assert item.raw_fields["product_name"] == "Adult Dog 成犬 雞肉配方 Cage-Free Chicken"

    # Two populated unlabeled columns are indistinguishable — refuse, never guess.
    ambiguous = _observation(row)
    two = ExtractedEvidence(
        observation_key="row-2",
        source_location=SourceLocation(row_number=1, source_object_key="row-2"),
        raw_cells=ambiguous.raw_cells
        + (RawCell(cell_reference=None, row_number=1, column_index=99, column_name="", raw_value="mystery"),),
        extraction_method=ExtractionMethod.MODEL_VISION,
        provider="test",
    )
    registration = get_supplier_source_contract(contract_id, "v1")
    contract = runtime.SupplierSourceRuntimeContract(declaration=registration.declaration)
    outcome = conform_observations((two,), (uuid4(),), contract)
    assert outcome.items[0].raw_fields["product_name"] is None


def test_canidae_transition_rows_conform_under_the_new_code():
    """Aliases match in declared order: on pages printing BOTH codes, the new
    code must win or the row conforms under the superseded SKU."""
    item = _conform_one(
        "kpn_trading.pack_price_list.v1",
        _observation(
            {
                "產品編號": "1005",
                "新產品編號": "1005J",
                "產品內容": "All Life Stages Dry Dog Food",
                "重量": "5 LB",
                "批發價 (HKD)": "$170",
                "建議零售價 (HKD)": "$230",
            }
        ),
    )
    assert item.normalized_fields["supplier_sku"]["value"] == "1005J"
    assert item.raw_fields["additional_fields"]["previous_supplier_sku"] == "1005"


def test_kangaroo_case_only_reads_annotated_case_total_as_case_basis():
    item = _conform_one(
        "kangaroo_pet_nutrition.unit_price_list.v1",
        _observation(
            {
                "產品編號": "CDL170",
                "產品內容": "Wet Lamb Recipe for Dogs\n羊肉配方",
                "重量": "170g",
                "批發價 (HKD) 每箱 (12罐)": "$340\n(@28.3)",
                "建議零售價 (HKD) 每罐": "$44",
            }
        ),
    )
    assert item.normalized_fields["cost"]["amount"] == "340"
    assert item.normalized_fields["cost"]["price_basis"]["code"] == "CASE"
    assert item.normalized_fields["rrp"]["amount"] == "44"
    # The raw layer keeps the printed compound cell untouched.
    assert item.raw_fields["cost"] == "$340\n(@28.3)"


def test_kangaroo_case_only_reads_the_spaceless_heading_spelling_too():
    """Vision captures the same headings WITHOUT spaces before the parens
    ('批發價(HKD) 每箱(12罐)'), and heading folding keeps spaces next to
    parens — so the spaceless spellings are aliased. This spelling stranded
    all 24 wet-can rows of run 1382e559 as CONTRACT_REQUIRED_FIELD_MISSING."""
    item = _conform_one(
        "kangaroo_pet_nutrition.unit_price_list.v1",
        _observation(
            {
                "產品編號": "CDL170",
                "產品內容": "Wet Lamb Recipe for Dogs 羊肉配方",
                "重量": "170g",
                "批發價(HKD) 每箱(12罐)": "$340",
                "建議零售價(HKD) 每罐": "$44",
            }
        ),
    )
    assert item.normalized_fields["cost"]["amount"] == "340"
    assert item.normalized_fields["cost"]["price_basis"]["code"] == "CASE"
    assert item.normalized_fields["rrp"]["amount"] == "44"


def test_vetapet_vet_price_basis_follows_the_order_unit_column():
    item = _conform_one(
        "vetapet.vet_price_list.v1",
        _observation(
            {
                "CODE NO": "510-0005-10",
                "PRODUCT NAME": "Canine Pancreatic Lipase (cPL) Rapid Test",
                "PACKING PER UNIT": "10 tests / box",
                "ORDER UNIT": "1 box",
                "UNIT PRICE": "HK$1958.0",
            }
        ),
    )
    assert item.normalized_fields["cost"]["amount"] == "1958.0"
    assert item.normalized_fields["cost"]["price_basis"]["code"] == "BOX"


def test_vetapet_by_quote_rows_carry_no_cost_and_flag_for_review():
    item = _conform_one(
        "vetapet.vet_price_list.v1",
        _observation(
            {
                "CODE NO": "1300-0005",
                "PRODUCT NAME": "VetScan VUE Rapid Test Analyzer",
                "PACKING PER UNIT": "1 set",
                "ORDER UNIT": "1 set",
                "UNIT PRICE": "By Quote",
            }
        ),
    )
    assert item.normalized_fields.get("cost") is None
    assert item.raw_fields["cost"] == "By Quote"
    assert any(i.issue_code == "CONTRACT_NULL_COST_REQUIRES_REVIEW" for i in item.issues)


def test_vetapet_retail_price_basis_stays_a_human_decision():
    """The retail section's carton note ('一箱6盒') is packaging info with an
    unconfirmed price basis — the row must keep its verbatim values and route
    to review rather than have a basis assumed for it."""
    item = _conform_one(
        "vetapet.non_vet_price_list.v1",
        _observation(
            {
                "編號": "10360",
                "產品": "海藻粉 500g (一箱6盒)",
                "批發價": "HK$64.00",
                "建議零售價": "HK$106.00",
            }
        ),
    )
    assert item.raw_fields["cost"] == "HK$64.00"
    assert item.raw_fields["product_name"] == "海藻粉 500g (一箱6盒)"
    assert any(i.issue_code == "CONTRACT_PRICE_BASIS_UNRESOLVED" for i in item.issues)


def test_identity_matching_survives_real_letterheads():
    """The letterheads the live suppliers actually print, against the names
    their contracts declare — the Vetapet pair is the one that dead-lettered
    on paper: 'vetapet' names the company on both sides while neither fold
    contains the other."""
    assert _identity_names_overlap("C. VETAPET & COMPANY 施惠德洋行", "Vetapet Vet")
    assert _identity_names_overlap("KPN Trading Ltd", "K.P.N. Trading")
    assert _identity_names_overlap("Hill's Pet Nutrition Asia Pacific", "Hill's")
    assert _identity_names_overlap("alfamedic.com.hk", "Alfamedic")
    # Distinct companies still mismatch: boilerplate and short tokens
    # (Company, Ltd, 'pet') never vouch for anyone.
    assert not _identity_names_overlap("C. VETAPET & COMPANY", "K.P.N. Trading")
    assert not _identity_names_overlap("Pet Shop Company Ltd", "Kangaroo Pet Nutrition")


def test_page_brand_reaches_the_brand_field_only_when_stamped():
    """Vetapet's brand is the wordmark heading the PAGE, not the table banner."""
    row = {
        "Code No": "401",
        "Product Name": "Revolution 15mg for Puppies & Kittens (Mauve 5 lbs or less)",
        "Packing Per Unit": "3 tubes / pack",
        "Unit Price": "HK$128.0",
    }
    branded = _conform_one(
        "vetapet.vet_price_list.v1",
        _observation(row, metadata={"page_brand_text": "zoetis"}),
    )
    assert branded.raw_fields["brand"] == "zoetis"

    bare = _conform_one("vetapet.vet_price_list.v1", _observation(row))
    assert not bare.raw_fields.get("brand")

    # The category banner must never masquerade as a brand again.
    sectioned = _conform_one(
        "vetapet.vet_price_list.v1",
        _observation(row, metadata={"section": "PARASITE CONTROL"}),
    )
    assert not sectioned.raw_fields.get("brand")


def test_a_measure_count_source_also_captures_the_content():
    """'30ml/ bot' counts 30 (the Alfamedic trade) AND prints a measure — the
    measure lands in packaging content so the export can say '30 ML / BOTTLE'
    instead of a naked '30 / BOTTLE'."""
    item = _conform_one(
        "alfamedic.price_list.v1",
        _observation({
            "Order Code": "EN7502",
            "Product Name": "Entyce® (capromorelin oral solution) 30mg/mL",
            "Brand": "Elanco",
            "Packing/ Unit": "30ml/ bot",
            "Order Units": "1 bot",
            "Price/ Unit (HKD)": "1390.0",
        }),
    )
    packaging = item.normalized_fields["packaging"]
    assert packaging["content_amount"] == "30"
    assert packaging["content_uom"]["code"] == "ML"

    # A countable pack text carries no measure — no content is invented.
    countable = _conform_one(
        "vetapet.vet_price_list.v1",
        _observation({
            "Code No": "401",
            "Product Name": "Revolution 15mg for Puppies & Kittens (Mauve 5 lbs or less)",
            "Packing Per Unit": "3 tubes / pack",
            "Unit Price": "HK$128.0",
        }),
    )
    assert "content_amount" not in countable.normalized_fields["packaging"]


def test_a_missing_banner_does_not_reject_the_price():
    """PR-18 closing audit, finding 1: brand is OPTIONAL on every KPN layout —
    a table whose banner the extraction missed must not dead-letter its rows."""
    item = _conform_one(
        "kpn_trading.pack_price_list.v1",
        _observation({
            "產品編號": "FRB-3",
            "產品名稱": "Stella's Super Beef 牛魔王",
            "原箱包數": "6包",
            "批發價 每包": "$204",
        }),
    )
    assert not [
        issue
        for issue in item.issues
        if issue.issue_code == "CONTRACT_REQUIRED_FIELD_MISSING" and "brand" in str(issue.message)
    ]


def test_a_previous_sku_equal_to_the_current_one_is_dropped():
    """PR-18 closing audit, finding 2: a rename to itself is not a rename.

    On ordinary pack_price_list pages only 產品編號 prints, and both the
    current and the previous SKU fields alias that heading — the fabricated
    previous==current claim is dropped. Renumbering pages that print both
    新產品編號 and 產品編號 keep the genuine transition."""
    import json as _json

    single = _conform_one(
        "kpn_trading.pack_price_list.v1",
        _observation({
            "產品編號": "SC001",
            "產品內容 Product Description": "Stella's Super Beef",
            "每包批發價 Wholesale Price Per Unit": "HK$113",
        }),
    )
    blob = _json.dumps({"raw": single.raw_fields, "norm": single.normalized_fields}, ensure_ascii=False)
    assert "SC001" in blob
    assert "previous_supplier_sku" not in blob, "a previous SKU equal to the current one is fabricated"

    renumbering = _conform_one(
        "kpn_trading.pack_price_list.v1",
        _observation({
            "新產品編號": "NEW-01",
            "產品編號": "OLD-01",
            "產品內容 Product Description": "Canidae transition row",
            "每包批發價 Wholesale Price Per Unit": "HK$99",
        }),
    )
    blob = _json.dumps({"raw": renumbering.raw_fields, "norm": renumbering.normalized_fields}, ensure_ascii=False)
    assert '"NEW-01"' in blob
    assert "OLD-01" in blob, "a genuine SKU transition must survive the guard"


# ─────────────────────────────────────────────────────────────────────────
# Page-banner promotions — a banner is typed only where the contract has
# declared its notation; everywhere else it stays verbatim page evidence.
# ─────────────────────────────────────────────────────────────────────────

_REVOLUTION_ROW = {
    "Code No": "401",
    "Product Name": "Revolution 15mg for Puppies & Kittens (Mauve 5 lbs or less)",
    "Packing Per Unit": "3 tubes / pack",
    "Unit Price": "HK$128.0",
}


def test_declared_page_banner_promotion_becomes_an_order_scope_term():
    """'Mix over $1000, 10% off' speaks about the ORDER, so every row of the
    page carries the same typed spend/percentage term."""
    item = _conform_one(
        "vetapet.vet_price_list.v1",
        _observation(
            _REVOLUTION_ROW,
            metadata={"page_promotion_text": "Promotion: Mix over $1000, 10% off"},
        ),
    )
    (term,) = item.normalized_fields["mbb_terms"]
    assert term["scope"] == "SUPPLIER_ORDER"
    assert term["condition"] == {
        "condition_type": "minimum_spend",
        "spend": {"amount": "1000", "currency": "HKD"},
    }
    assert term["benefit"] == {"benefit_type": "percentage_discount", "percentage": "10"}
    assert term["description"] == "Promotion: Mix over $1000, 10% off"


def test_mixed_quantity_zhe_tiers_translate_to_percent_off():
    """混合12件 9折 24件 8折 — 9折 means pay 90%, so 12 items unlock 10% off
    and 24 items unlock 20% off; both tiers are separate order-scope terms."""
    item = _conform_one(
        "vetapet.vet_price_list.v1",
        _observation(_REVOLUTION_ROW, metadata={"page_promotion_text": "混合12件 9折 24件 8折"}),
    )
    first, second = item.normalized_fields["mbb_terms"]
    for term in (first, second):
        assert term["scope"] == "SUPPLIER_ORDER"
        assert term["condition"]["condition_type"] == "minimum_quantity"
        assert term["benefit"]["benefit_type"] == "percentage_discount"
        assert term["description"] == "混合12件 9折 24件 8折"
    assert first["condition"]["quantity"]["amount"] == "12"
    assert first["benefit"]["percentage"] == "10"
    assert second["condition"]["quantity"]["amount"] == "24"
    assert second["benefit"]["percentage"] == "20"


def test_banner_on_an_undeclared_contract_stays_evidence():
    """KPN declares no page_promotion_shapes, so a stamped banner parses into
    nothing — the row keeps only its own case-tier term."""
    item = _conform_one(
        "kpn_trading.pack_price_list.v1",
        _observation(
            _FRB_ROW,
            metadata={"page_promotion_text": "Promotion: Mix over $1000, 10% off"},
        ),
    )
    scopes = [term["scope"] for term in item.normalized_fields["mbb_terms"]]
    assert scopes == ["SUPPLIER_SKU"], "the banner must not add order terms on an undeclared contract"


def test_unrecognised_banner_text_parses_into_nothing():
    """A banner in a notation the contract did not declare is preserved as
    evidence, never guessed into a term."""
    item = _conform_one(
        "vetapet.vet_price_list.v1",
        _observation(
            _REVOLUTION_ROW,
            metadata={"page_promotion_text": "Summer clearance — everything must go"},
        ),
    )
    assert item.normalized_fields["mbb_terms"] == []


# ─────────────────────────────────────────────────────────────────────────
# Same-code quantity bands — the deeper band row is an MBB term on the base
# row, never a second candidate whose publication would supersede the base.
# ─────────────────────────────────────────────────────────────────────────

_TOPIZOLE_BASE = {
    "CODE NO": "TOP250",
    "PRODUCT NAME": "Topizole 5mg/ml Oral Suspension 30ml",
    "ORDER UNIT": "1-10 bottles",
    "UNIT PRICE": "HK$82.0",
}
_TOPIZOLE_DEEP = {
    "CODE NO": "TOP250",
    "PRODUCT NAME": "Topizole 5mg/ml Oral Suspension 30ml",
    "ORDER UNIT": "11-20 bottles",
    "UNIT PRICE": "HK$78.0",
}


def _conform_many(contract_id: str, rows: list[dict[str, str]]):
    registration = get_supplier_source_contract(contract_id, "v1")
    contract = runtime.SupplierSourceRuntimeContract(declaration=registration.declaration)
    observations = tuple(_observation(cells, key=f"row-{i}") for i, cells in enumerate(rows, start=1))
    return conform_observations(observations, tuple(uuid4() for _ in rows), contract)


def test_same_code_quantity_band_folds_into_a_term_on_the_base_row():
    """TOP250's '11-20 bottles' row: buy eleven, pay 78 per bottle — a term,
    not a duplicate candidate."""
    out = _conform_many("vetapet.vet_price_list.v1", [_TOPIZOLE_BASE, _TOPIZOLE_DEEP])
    (item,) = out.items
    assert item.normalized_fields["cost"]["amount"] == "82.0"
    assert item.normalized_fields["cost"]["price_basis"]["code"] == "BOTTLE"
    (term,) = item.normalized_fields["mbb_terms"]
    assert term["scope"] == "SUPPLIER_SKU"
    assert term["condition"]["condition_type"] == "minimum_quantity"
    assert term["condition"]["quantity"]["amount"] == "11"
    assert term["condition"]["quantity"]["uom"]["code"] == "BOTTLE"
    assert term["benefit"]["discounted_price"]["amount"] == "78.0"
    assert term["description"] == "11-20 bottles"
    assert out.metadata["skipped_quantity_band_rows"] == 1


def test_three_bands_fold_into_two_terms_on_one_base():
    third = dict(_TOPIZOLE_DEEP, **{"ORDER UNIT": "21-40 bottles", "UNIT PRICE": "HK$75.0"})
    out = _conform_many("vetapet.vet_price_list.v1", [_TOPIZOLE_BASE, _TOPIZOLE_DEEP, third])
    (item,) = out.items
    starts = [t["condition"]["quantity"]["amount"] for t in item.normalized_fields["mbb_terms"]]
    prices = [t["benefit"]["discounted_price"]["amount"] for t in item.normalized_fields["mbb_terms"]]
    assert starts == ["11", "21"]
    assert prices == ["78.0", "75.0"]
    assert out.metadata["skipped_quantity_band_rows"] == 2


def test_a_band_that_is_not_cheaper_does_not_fold():
    """A 'discount' at the same price would corrupt downstream costs — both
    rows stay candidates for a person."""
    dearer = dict(_TOPIZOLE_DEEP, **{"UNIT PRICE": "HK$82.0"})
    out = _conform_many("vetapet.vet_price_list.v1", [_TOPIZOLE_BASE, dearer])
    assert len(out.items) == 2
    assert out.metadata["skipped_quantity_band_rows"] == 0


def test_a_repeated_code_without_a_base_band_stays_two_candidates():
    """A duplicate code where the earlier row prints no band is a duplicate to
    review, not a ladder to fold."""
    plain_base = dict(_TOPIZOLE_BASE, **{"ORDER UNIT": "1 bottle"})
    out = _conform_many("vetapet.vet_price_list.v1", [plain_base, _TOPIZOLE_DEEP])
    assert len(out.items) == 2
    assert out.metadata["skipped_quantity_band_rows"] == 0


# ─────────────────────────────────────────────────────────────────────────
# Struck-price Special Offers — two unmarked amounts parse ONLY where the
# contract declared that render; everywhere else the refusal stands.
# ─────────────────────────────────────────────────────────────────────────


def test_a_declared_struck_price_cell_becomes_cost_plus_special_offer():
    """'$739 HK$1056.0' under Vetapet's declaration: the larger amount is the
    regular cost, the smaller an unconditional Special-Offer term."""
    item = _conform_one(
        "vetapet.vet_price_list.v1",
        _observation({
            "CODE NO": "EAB10",
            "PRODUCT NAME": "Special Offer sample product",
            "ORDER UNIT": "1 bottle",
            "UNIT PRICE": "$739 HK$1056.0",
        }),
    )
    assert item.normalized_fields["cost"]["amount"] == "1056.0"
    assert item.normalized_fields["cost"]["price_basis"]["code"] == "BOTTLE"
    (term,) = item.normalized_fields["mbb_terms"]
    assert term["scope"] == "SUPPLIER_SKU"
    assert term["condition"]["condition_type"] == "minimum_quantity"
    assert term["condition"]["quantity"]["amount"] == "1"
    assert term["benefit"]["discounted_price"]["amount"] == "739"
    assert term["benefit"]["discounted_price"]["price_basis"]["code"] == "BOTTLE"
    assert term["description"] == "$739 HK$1056.0"


def test_two_unmarked_amounts_still_refuse_on_undeclared_contracts():
    """The KPN/Kangaroo hardening is untouched: without the declaration, two
    printed prices stay a guess the parser will not make."""
    item = _conform_one(
        "kpn_trading.pack_price_list.v1",
        _observation({
            "產品編號": "SC001",
            "產品內容 Product Description": "Stella & Chewy Chicken Dinner",
            "每包批發價 Wholesale Price Per Unit": "$99 HK$120.0",
        }),
    )
    assert "cost" not in item.normalized_fields
    assert item.normalized_fields["mbb_terms"] == []


def test_struck_price_guards_refuse_equal_or_extra_amounts():
    """Equal amounts are no discount; three amounts are a misread — both keep
    dead-lettering for a person even under the declaration."""
    for printed in ("$1056 HK$1056.0", "$739 HK$1056.0 $88"):
        item = _conform_one(
            "vetapet.vet_price_list.v1",
            _observation({
                "CODE NO": "EAB10",
                "PRODUCT NAME": "Special Offer sample product",
                "ORDER UNIT": "1 bottle",
                "UNIT PRICE": printed,
            }),
        )
        assert "cost" not in item.normalized_fields, printed
        assert item.normalized_fields["mbb_terms"] == [], printed


def test_case_quantity_reads_the_counter_not_the_leading_size():
    """The real frozen-raw pages merge size and count ('12lb 2包' = two 12-lb
    bags per case): the tier quantity is the number wearing the counter noun,
    never the leading size — and the heading prints WITHOUT a space before
    the parenthesis, which the alias covers."""
    for packing, price_cell, want_qty, want_price in (
        ("3lb 6包", "$1094/箱 ($182/包)", "6", "182"),
        ("12lb 2包", "$1306/箱 ($653/包)", "2", "653"),
        ("1.25lb 4包", "$336/箱 ($84/包)", "4", "84"),
    ):
        item = _conform_one(
            "kpn_trading.pack_price_list.v1",
            _observation({
                "產品編號": "FRX-1",
                "產品名稱": "Frozen sample",
                "原箱包數": packing,
                "批發價 每包": "$204" if want_qty != "2" else "$679",
                "批發價 每箱(平均每包價)": price_cell,
                "建議零售價 每包": "$276",
            }),
        )
        (term,) = item.normalized_fields["mbb_terms"]
        assert term["condition"]["quantity"]["amount"] == want_qty, packing
        assert term["benefit"]["discounted_price"]["amount"] == want_price, packing


def test_good_gravy_rows_take_their_name_from_the_section_heading():
    """The Good Gravy layout prints the product name ONLY as the block heading
    above the rows (user-confirmed against the rendered page 47): description
    composes from section_name when no name column resolves — and never
    overrides a printed name column where one exists."""
    item = _conform_one(
        "kpn_trading.pack_price_list.v1",
        _observation(
            {
                "barcode#": "8 15260 00767 2",
                "包裝": "3.5lb",
                "sku#": "FG00654",
                "批發價": "$215",
                "零售價": "$310",
            },
            metadata={"section": "ADULT DOG BEEF 成犬 香濃火雞骨湯外層乾糧 - 牛肉配方(含古代穀物)"},
        ),
    )
    assert item.raw_fields["product_name"] == "ADULT DOG BEEF 成犬 香濃火雞骨湯外層乾糧 - 牛肉配方(含古代穀物)"
    assert item.raw_fields["barcode"] == "8 15260 00767 2"
    assert item.normalized_fields["cost"]["amount"] == "215"

    named = _conform_one(
        "kpn_trading.pack_price_list.v1",
        _observation(
            {
                "產品編號": "SC001",
                "產品內容": "Stella's Super Beef 牛魔王",
                "批發價": "HK$113",
            },
            metadata={"section": "SOME SECTION BANNER"},
        ),
    )
    assert named.raw_fields["product_name"] == "Stella's Super Beef 牛魔王"


# ─────────────────────────────────────────────────────────────────────────
# Quantity-ladder price columns — the indent-order sections price whole rows
# as a ladder; ruling 2026-08-17: lowest filled rung = base cost (+ minimum
# order when its bound exceeds 1), deeper rungs = terms.
# ─────────────────────────────────────────────────────────────────────────


def test_vaccine_ladder_lowest_rung_is_cost_and_its_bound_is_the_moq():
    """VANG-B: 50+ at $41 (per unit -> per dose), 300+ at $39 becomes a term."""
    item = _conform_one(
        "vetapet.vet_price_list.v1",
        _observation({
            "Code No": "VANG-B",
            "Product Name": "Vanguard B (Intranasal)(Kennel Cough)",
            "Packing Per Unit": "25 doses / pack",
            "PRICE: 50 doses or above (per unit)": "HK$41.0",
            "PRICE: 100 doses or above (per unit)": "----",
            "PRICE: 300 doses or above (per unit)": "HK$39.0",
        }),
    )
    cost = item.normalized_fields["cost"]
    assert cost["amount"] == "41.0"
    assert cost["price_basis"]["code"] == "UNIT", "the heading says per unit — never the packing's /pack"
    packaging = item.normalized_fields["packaging"]
    assert packaging["minimum_order_quantity"] == {"amount": "50", "uom": cost["price_basis"]}
    (term,) = item.normalized_fields["mbb_terms"]
    assert term["condition"]["quantity"]["amount"] == "300"
    assert term["benefit"]["discounted_price"]["amount"] == "39.0"


def test_ladder_skips_empty_rungs_when_choosing_the_base():
    """VANG5CVL prints nothing at 50+: the lowest FILLED rung (100+) is base."""
    item = _conform_one(
        "vetapet.vet_price_list.v1",
        _observation({
            "Code No": "VANG5CVL",
            "Product Name": "Vanguard Plus 5CV-L (DHPPi/L2 + CV)",
            "Packing Per Unit": "25 doses / pack",
            "PRICE: 50 doses or above (per unit)": "----",
            "PRICE: 100 doses or above (per unit)": "HK$39.0",
            "PRICE: 300 doses or above (per unit)": "HK$36.0",
        }),
    )
    assert item.normalized_fields["cost"]["amount"] == "39.0"
    assert item.normalized_fields["packaging"]["minimum_order_quantity"]["amount"] == "100"
    (term,) = item.normalized_fields["mbb_terms"]
    assert term["condition"]["quantity"]["amount"] == "300"


def test_drug_ladder_starting_at_one_has_no_moq_and_keeps_the_row_basis():
    """RIM-25: 'PRICE: 1-2' is the base (a bound of 1 is no constraint) and the
    price is per BOTTLE — no '(per unit)' in these headings."""
    item = _conform_one(
        "vetapet.vet_price_list.v1",
        _observation({
            "Code No": "RIM-25",
            "Product Name": "Rimadyl Chewable Tablets 25mg",
            "Packing Per Unit": "60 tab / bottle",
            "PRICE: 1-2": "HK$116.0",
            "PRICE: 3-9": "HK$106.0",
            "PRICE: 10 or above": "HK$101.0",
        }),
    )
    cost = item.normalized_fields["cost"]
    assert cost["amount"] == "116.0"
    assert cost["price_basis"]["code"] == "BOTTLE"
    assert "minimum_order_quantity" not in item.normalized_fields["packaging"]
    terms = item.normalized_fields["mbb_terms"]
    assert [(t["condition"]["quantity"]["amount"], t["benefit"]["discounted_price"]["amount"]) for t in terms] == [
        ("3", "106.0"), ("10", "101.0"),
    ]


def test_single_rung_price_tables_degenerate_to_a_plain_cost():
    item = _conform_one(
        "vetapet.vet_price_list.v1",
        _observation({
            "Code No": "JCH-C",
            "Product Name": "Chlorhex-C Antiseptic/Disinfectant Concentrate",
            "Price: 1 or above": "HK$590",
        }),
    )
    assert item.normalized_fields["cost"]["amount"] == "590"
    assert item.normalized_fields["mbb_terms"] == []


def test_diagnostics_regular_box_price_is_the_cost_and_the_badge_price_is_not():
    """The diagnostics Special-Offer tables were captured as two columns:
    'Unit Price (single)' is the 30%-off promo badge (verified: every value is
    exactly 0.70 x the box price), 'Unit Price (box)' the regular price. Per
    the struck-price ruling the REGULAR price is the cost; the badge stays
    evidence until its offer-term treatment is confirmed."""
    clean = _conform_one(
        "vetapet.vet_price_list.v1",
        _observation({
            "Code No": "510-0005-10",
            "Product Name": "Canine Pancreatic Lipase (cPL) Rapid Test",
            "Packing Per Unit": "10 tests / box",
            "Order Unit": "1 box",
            "Unit Price (single)": None,
            "Unit Price (box)": "HK$1958.0",
        }),
    )
    assert clean.normalized_fields["cost"]["amount"] == "1958.0"
    assert clean.normalized_fields["cost"]["price_basis"]["code"] == "BOX"

    offered = _conform_one(
        "vetapet.vet_price_list.v1",
        _observation({
            "Code No": "200-1501",
            "Product Name": "Rapid Test sample",
            "Packing Per Unit": "10 tests / box",
            "Order Unit": "1 box",
            "Unit Price (single)": "$739",
            "Unit Price (box)": "HK$1056.0",
        }),
    )
    assert offered.normalized_fields["cost"]["amount"] == "1056.0", "the regular price, never the badge"


def test_vetapet_wholesale_retail_family_prices_per_unit():
    """User ruling 2026-08-25, extending the earlier rrp-column ruling: on the
    Wholesale/Retail tables (批發價 beside 建議零售價 — bare, letter-spaced, or
    with the parenthesised English), the wholesale IS the per-unit price, even
    when the product text names a case ('1箱6罐'). These rows sat held as
    cost-missing because the family was deliberately undeclared until the
    basis had a ruling."""
    item = _conform_one(
        "vetapet.vet_price_list.v1",
        _observation(
            {
                "編號 (Code)": "15201",
                "產品 (Product)": "LACTOL 力圖犬奶粉 250g (1箱6罐)",
                "批發價 (Wholesale)": "HKD$69",
                "建議零售價 (Retail)": "HKD$127",
            }
        ),
    )
    assert item.normalized_fields["supplier_sku"]["value"] == "15201"
    assert item.normalized_fields["cost"]["amount"] == "69"
    assert item.normalized_fields["cost"]["price_basis"]["code"] == "UNIT"
    assert item.normalized_fields["rrp"]["amount"] == "127"
    assert not [i for i in item.issues if i.severity == "BLOCKING"]


def test_vetapet_bilingual_treat_layout_conforms_prices_and_box_stays_evidence():
    """The treat layout (Product Name (bilingual) / 重量 1 / 批發價 1 /
    建議零售價 1 / 量 2 (Box) / 盒批發價 2): the numbered wholesale beside its
    numbered retail is the per-piece price (2026-08-25 ruling), the box pair
    is a purchase-format statement captured as evidence — NEVER a term (the
    box total is exactly piece × count; no printed per-piece rate at box
    quantity), and the page prints no code, so the row holds on supplier_sku
    alone — which the desk offers as an addable column."""
    item = _conform_one(
        "vetapet.vet_price_list.v1",
        _observation(
            {
                "Product Name (bilingual)": "天然火雞筋打結骨 Natural Turkey Tendon Bone (variant 2)",
                "重量 1 (Weight)": "1條",
                "批發價 1": "$19.6",
                "建議零售價 1": "$38",
                "量 2 (Box)": "1盒20條",
                "盒批發價 2": "批發價$392",
            }
        ),
    )
    assert item.normalized_fields["cost"]["amount"] == "19.6"
    assert item.normalized_fields["cost"]["price_basis"]["code"] == "UNIT"
    assert item.normalized_fields["rrp"]["amount"] == "38"
    assert "Natural Turkey Tendon Bone" in item.normalized_fields["product_name"]["value"]
    assert not item.normalized_fields.get("mbb_terms"), "a box total is never a deal"
    extra = item.raw_fields["additional_fields"]
    assert extra["box_quantity"] == "1盒20條"
    assert extra["box_wholesale_price"] == "批發價$392"
    missing = {i.field_key for i in item.issues if i.issue_code == "CONTRACT_REQUIRED_FIELD_MISSING"}
    assert missing == {"supplier_sku"}, "the printed page has no code — the only honest hold"

    from services.catalogue_conformance import addable_required_columns
    from schemas.catalogue_pipeline.supplier_contracts import get_supplier_source_contract as _get
    contract = runtime.SupplierSourceRuntimeContract(_get("vetapet.vet_price_list.v1", "v1").declaration)
    cells = [{"column_name": name} for name in (
        "Product Name (bilingual)", "重量 1 (Weight)", "批發價 1", "建議零售價 1", "量 2 (Box)", "盒批發價 2",
    )]
    offers = addable_required_columns(contract, cells)
    assert [(o.field_key, o.column_name) for o in offers] == [("supplier_sku", "CODE NO / 編號")]


def test_vetapet_goods_accessory_layout_conforms():
    """The accessories pages (貨品編號 (Code no.) / 貨品名稱 (Name) / 批發價
    (Wholesale price) / 建議零售價 (Recommended Retail price)) — 32 held rows
    whose only blocker was the unaliased code heading. Same per-unit ruling:
    retail beside wholesale. The full code heading is aliased; bare 'Code No.'
    (the legend sidebars') is still not an alias of its own."""
    item = _conform_one(
        "vetapet.vet_price_list.v1",
        _observation(
            {
                "貨品編號 (Code no.)": "75732017",
                "貨品名稱 (Name)": "伸縮扁拖 AMIGO TAPE 黑色 - L (INNER 1-5 m - Max 50kg)",
                "批發價 (Wholesale price)": "$186.0",
                "建議零售價 (Recommended Retail price)": "$298",
            }
        ),
    )
    assert item.normalized_fields["supplier_sku"]["value"] == "75732017"
    assert item.normalized_fields["cost"]["amount"] == "186.0"
    assert item.normalized_fields["cost"]["price_basis"]["code"] == "UNIT"
    assert item.normalized_fields["rrp"]["amount"] == "298"
    assert "AMIGO TAPE" in item.normalized_fields["product_name"]["value"]
    assert not [i for i in item.issues if i.severity == "BLOCKING"]
