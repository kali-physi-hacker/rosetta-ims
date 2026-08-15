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
    contract_id = "kpn_trading.pack_and_case_bulk_list.v1"
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
    item = _conform_one("kpn_trading.pack_and_case_bulk_list.v1", _observation(_FRB_ROW))
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
    item = _conform_one("kpn_trading.pack_and_case_bulk_list.v1", _observation(row))
    assert item.normalized_fields["mbb_terms"] == []


def test_unlabeled_column_resolves_only_when_exactly_one_value_exists():
    contract_id = "kpn_trading.pack_and_case_bulk_list.v1"
    single = dict(_FRB_ROW)
    single[""] = "3lb"  # the frozen-raw size column prints no heading
    item = _conform_one(contract_id, _observation(single))
    assert item.raw_fields["packaging"] == "3lb"

    # Two populated unlabeled columns are indistinguishable — refuse, never guess.
    ambiguous = _observation(
        {**_FRB_ROW, "": "3lb"},
    )
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
    assert outcome.items[0].raw_fields["packaging"] is None


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
        "kangaroo_pet_nutrition.case_only_price_list.v1",
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
        "kpn_trading.pack_and_case_bulk_list.v1",
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
