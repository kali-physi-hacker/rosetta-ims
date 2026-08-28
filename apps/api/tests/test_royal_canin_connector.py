"""Royal Canin: a supplier read from a webshop, conformed like any other.

Royal Canin issues no price list, so the connector reads their B2B webshop's
product index into a CSV snapshot and the ordinary pipeline takes it from
there. What these tests pin is everything that could quietly go wrong in that
translation:

* only OUR customer group's catalogue and OUR price are written;
* the no-account sentinel never becomes money;
* an unchanged catalogue produces identical bytes, so re-reading submits nothing;
* a half-finished read is not allowed to look like a delisting;
* and the row's own unit decides the price basis — UNIT prices one item,
  INNER BOX prices a case — because that is the difference between $105 a
  pouch and $105 for twelve.
"""

from __future__ import annotations

import csv
import io
import os
import tempfile

os.environ.setdefault("DATABASE_URL", f"sqlite:///{tempfile.mkdtemp()}/royal_canin.db")

import pytest  # noqa: E402

from schemas.catalogue_pipeline.enums import UnitCode  # noqa: E402
from services import royal_canin_connector as rc  # noqa: E402

GROUP = "879"


def _hit(**overrides):
    """One index hit shaped exactly like the live shop's."""
    hit = {
        "objectID": "246013",
        "original_sku": "4200600",
        "sku": "HK_4200600",
        "name": "FHN CAT AGEING 15+ P 85GX12",
        "ean_code": "9003579050163",
        "nav_uom": "INNER BOX",
        "navision_weight": 1.02,
        "gtm_category": "CAT",
        "nav_animal_type": "CAT",
        "categories": {"level0": ["VET CAT 獸醫系列 -  貓", "Buy again 再次購買"]},
        "customer_groups_ids": ["879", "881"],
        "in_stock": True,
        "qty_in_stock": 40,
        "stock_configuration": {"min_sale_qty": 1, "qty_increments": 0},
        "url": "https://webshop.royalcanin.com/hk/en/hk-4200600-fhn-cat-ageing-15-p-85gx12",
        "price": {"HKD": {"default": rc.NO_PRICE_SENTINEL, "group_879_tier": 105, "group_881_tier": 120}},
    }
    hit.update(overrides)
    return hit


def _rows(snapshot: rc.Snapshot) -> list[dict]:
    return list(csv.DictReader(io.StringIO(snapshot.csv_bytes.decode("utf-8"))))


def test_the_snapshot_carries_our_group_price_and_never_the_sentinel():
    snapshot = rc.build_snapshot([_hit()], customer_group=GROUP, captured_on="2026-08-27")
    row = _rows(snapshot)[0]

    assert row["original_sku"] == "4200600"
    assert row["price_hkd"] == "105"          # ours, not group 881's 120
    assert row["ean_code"] == "9003579050163"
    assert row["nav_uom"] == "INNER BOX"
    assert str(rc.NO_PRICE_SENTINEL) not in snapshot.csv_bytes.decode("utf-8")


def test_products_belonging_to_other_groups_are_not_our_catalogue():
    """Their own pages 404 for us — they must never reach the desk."""
    theirs = _hit(original_sku="4109500", customer_groups_ids=["881", "991"],
                  price={"HKD": {"group_881_tier": 354}})
    snapshot = rc.build_snapshot([_hit(), theirs], customer_group=GROUP, captured_on="2026-08-27")

    codes = [row["original_sku"] for row in _rows(snapshot)]
    assert codes == ["4200600"]


def test_a_product_with_no_price_for_us_is_kept_and_reported():
    """Kept for a person to see; the connector does not silently drop products."""
    unpriced = _hit(original_sku="9999999", price={"HKD": {"default": rc.NO_PRICE_SENTINEL}})
    snapshot = rc.build_snapshot([_hit(), unpriced], customer_group=GROUP, captured_on="2026-08-27")

    rows = {row["original_sku"]: row for row in _rows(snapshot)}
    assert rows["9999999"]["price_hkd"] == ""
    note = next(w for w in snapshot.warnings if "9999999" in w.message)
    # Coded, not just worded: an unpriced product and a dual-filed one ask a
    # person for different things, and a screen given only sentences would
    # report one as the other.
    assert note.code == rc.WARN_NO_PRICE


def test_the_same_catalogue_always_produces_the_same_bytes():
    """So an unchanged catalogue submits nothing instead of a duplicate run."""
    first = rc.build_snapshot([_hit(), _hit(original_sku="1111111")],
                              customer_group=GROUP, captured_on="2026-08-27")
    # Same products, opposite order, read on a different day.
    second = rc.build_snapshot([_hit(original_sku="1111111"), _hit()],
                               customer_group=GROUP, captured_on="2026-09-04")

    assert first.checksum == second.checksum
    assert first.filename != second.filename  # the date names the file, never the checksum


def test_a_price_change_changes_the_checksum():
    before = rc.build_snapshot([_hit()], customer_group=GROUP, captured_on="2026-08-27")
    after = rc.build_snapshot(
        [_hit(price={"HKD": {"group_879_tier": 111}})], customer_group=GROUP, captured_on="2026-08-27")

    assert before.checksum != after.checksum


def test_a_part_finished_read_may_not_look_like_a_delisting():
    """The one failure mode a fetch has that a file does not."""
    healthy = rc.assess_completeness(current_rows=450, previous_rows=454)
    truncated = rc.assess_completeness(current_rows=200, previous_rows=454)

    assert healthy.trustworthy
    assert not truncated.trustworthy
    assert "must not be treated as proof" in truncated.reason
    # A first read has nothing to compare against and is allowed.
    assert rc.assess_completeness(current_rows=454, previous_rows=None).trustworthy


def test_an_empty_catalogue_is_an_error_not_an_empty_snapshot(monkeypatch):
    """With BOTH suppliers empty, the read is refused rather than queueing two
    empty catalogues."""
    monkeypatch.setattr(rc, "fetch_index", lambda **kwargs: [_hit(customer_groups_ids=["991"])])
    with pytest.raises(rc.RoyalCaninConnectorError, match="none belong to customer group"):
        rc.capture_snapshots(captured_on="2026-08-27", customer_group=GROUP)


def test_verify_credentials_reads_the_shop_back():
    html = (
        'window.CgiTool.algoliaConfig = {"app_id":"GD1LHQ79KB",'
        '"search_only_api_key":"565ad0e43990661ca61cac57dc0e0073","index_prefix":"rcb2b_",'
        '"product_index_name":"rcb2b_royalcanin_hk_en_products"};'
        ' ... "group_id":"879" ...'
    )
    found = rc.verify_credentials(html)

    assert found["app_id"] == "GD1LHQ79KB"
    assert found["customer_group"] == "879"
    assert found["matches_current_settings"] is True
    # A rotated key is reported as a mismatch rather than silently accepted.
    assert rc.verify_credentials(html.replace("565ad0e43990661ca61cac57dc0e0073", "0" * 32))[
        "matches_current_settings"
    ] is False


# ── the part that matters commercially: what one price buys ────────────────

def _conform(snapshot: rc.Snapshot):
    """Run the snapshot through extraction + conformance, as ingestion does."""
    from schemas.catalogue_pipeline.supplier_contracts.suppliers import (
        ROYAL_CANIN_VET_WEBSHOP_SNAPSHOT_V1,
    )
    from services.catalogue_conformance import conform_observations
    from services.catalogue_evidence_extraction import extract_evidence
    from services.supplier_source_contract_runtime import SupplierSourceRuntimeContract
    from uuid import uuid4

    result = extract_evidence(snapshot.csv_bytes, snapshot.filename, "text/csv")
    assert result.observations, "the snapshot produced no observations"
    runtime = SupplierSourceRuntimeContract(declaration=ROYAL_CANIN_VET_WEBSHOP_SNAPSHOT_V1)
    return conform_observations(
        result.observations, tuple(uuid4() for _ in result.observations), runtime
    )


def test_the_rows_own_unit_decides_what_the_price_buys():
    """UNIT prices one item; INNER BOX prices a case. Same column, different basis."""
    case_row = _hit()  # INNER BOX, HK$105 for 12 pouches
    unit_row = _hit(
        original_sku="4108800", sku="HK_4108800", name="FHN CAT AGEING 15+ 2KG",
        ean_code="3182550786126", nav_uom="UNIT", navision_weight=2,
        price={"HKD": {"group_879_tier": 225}},
    )
    snapshot = rc.build_snapshot([case_row, unit_row], customer_group=GROUP, captured_on="2026-08-27")

    conformed = _conform(snapshot)
    by_sku = {
        row.raw_fields.get("supplier_sku"): row
        for row in conformed.items
        if row.raw_fields.get("supplier_sku")
    }

    case = by_sku["4200600"]
    unit = by_sku["4108800"]
    assert case.normalized_fields["cost"]["price_basis"]["code"] == UnitCode.CASE.value
    assert unit.normalized_fields["cost"]["price_basis"]["code"] == UnitCode.UNIT.value
    assert case.normalized_fields["cost"]["amount"] in ("105", "105.0", "105.00")
    assert unit.normalized_fields["cost"]["amount"] in ("225", "225.0", "225.00")


def test_an_unknown_unit_holds_the_row_instead_of_guessing_one():
    """A unit Royal Canin invents is a decision for a person, not a default."""
    strange = _hit(original_sku="7777777", nav_uom="PALLET")
    snapshot = rc.build_snapshot([strange], customer_group=GROUP, captured_on="2026-08-27")

    conformed = _conform(snapshot)
    codes = {issue.issue_code for row in conformed.items for issue in row.issues}
    assert "CONTRACT_PRICE_BASIS_UNRESOLVED" in codes


# ── the two suppliers Royal Canin actually invoices ────────────────────────

def _retail_hit(**overrides):
    hit = _hit(original_sku="4108800", name="FHN CAT AGEING 15+ 2KG", nav_uom="UNIT")
    hit["categories"] = {"level0": ["PET SHOP CAT 貓糧 ", "Buy again 再次購買"]}
    hit.update(overrides)
    return hit


def test_each_supplier_gets_only_the_products_it_invoices():
    """Vet and retail bill on different rebate ladders — a row filed under the
    wrong supplier earns the wrong terms, so the split follows Royal Canin's
    own channel filing rather than our guess."""
    hits = [_hit(), _retail_hit()]

    vet = rc.build_snapshot(hits, customer_group=GROUP, captured_on="2026-08-28",
                            product_range=rc.RANGE_VET)
    retail = rc.build_snapshot(hits, customer_group=GROUP, captured_on="2026-08-28",
                               product_range=rc.RANGE_NON_VET)

    assert [row["original_sku"] for row in _rows(vet)] == ["4200600"]
    assert [row["original_sku"] for row in _rows(retail)] == ["4108800"]
    assert vet.product_range == rc.RANGE_VET and retail.product_range == rc.RANGE_NON_VET
    # The two snapshots are separate documents with separate identities.
    assert vet.checksum != retail.checksum
    assert "vet" in vet.filename and "non-vet" in retail.filename


def test_the_accounts_own_reorder_list_is_not_a_sales_channel():
    """"Buy again" sits on nearly every row. Counted as a retail channel it
    would call almost every veterinary product dual-filed."""
    assert rc.classify(_hit()) == (rc.RANGE_VET, False)
    assert rc.classify(_retail_hit()) == (rc.RANGE_NON_VET, False)


def test_a_product_filed_under_both_channels_is_reported_not_hidden():
    both = _hit(original_sku="2736300", name="BABY CAT MILK 300G")
    both["categories"] = {"level0": ["VET CAT 獸醫系列 -  貓", "PET SHOP CAT 貓糧 "]}

    product_range, dual = rc.classify(both)
    assert (product_range, dual) == (rc.RANGE_VET, True)
    snapshot = rc.build_snapshot([both], customer_group=GROUP, captured_on="2026-08-28",
                                 product_range=rc.RANGE_VET)
    note = next(w for w in snapshot.warnings if w.code == rc.WARN_DUAL_LISTED)
    assert "both a veterinary and a retail channel" in note.message
    assert "2736300" in note.message


def test_an_uncategorised_row_falls_back_to_the_product_line():
    """Nothing filed it, so the veterinary lines name themselves — misfiling a
    vet product as retail would attach the wrong rebate."""
    bare = _hit(name="VHN DOG RENAL 2KG")
    bare["categories"] = {}
    assert rc.classify(bare)[0] == rc.RANGE_VET
    bare["name"] = "SHN DOG MEDIUM ADULT 4KG"
    assert rc.classify(bare)[0] == rc.RANGE_NON_VET


# ── the parts that silently rewrite money or the supplier ──────────────────

def test_money_is_written_with_the_source_digits_not_six_of_them():
    """The snapshot's price string IS the cost the pipeline publishes.

    "%g" formatting keeps six significant figures, which turns 12345.67 into
    12345.7 and ten million into "1e+07" — a rounding nobody asked for, applied
    to a supplier's trade price on its way to a margin.
    """
    for amount, expected in (
        (105, "105"),
        (2999.95, "2999.95"),
        (12345.67, "12345.67"),
        (123456.78, "123456.78"),
        (10000000, "10000000"),
    ):
        snapshot = rc.build_snapshot(
            [_hit(price={"HKD": {"group_879_tier": amount}})],
            customer_group=GROUP, captured_on="2026-08-27",
        )
        assert _rows(snapshot)[0]["price_hkd"] == expected


def test_the_no_account_sentinel_is_not_money_even_spelled_as_a_string():
    """Checked after conversion, never against the raw field.

    A JSON number and the string "999999999" are the same non-price. Comparing
    the raw value catches only the first, and the second reaches conformance as
    a clean HK$999,999,999 cost with no issue raised against it.
    """
    for sentinel in (rc.NO_PRICE_SENTINEL, str(rc.NO_PRICE_SENTINEL), float(rc.NO_PRICE_SENTINEL)):
        snapshot = rc.build_snapshot(
            [_hit(price={"HKD": {"group_879_tier": sentinel}})],
            customer_group=GROUP, captured_on="2026-08-27",
        )
        assert _rows(snapshot)[0]["price_hkd"] == "", f"{sentinel!r} was written as money"
        assert any(w.code == rc.WARN_NO_PRICE for w in snapshot.warnings)


def test_a_renamed_veterinary_channel_still_files_under_the_vet_account():
    """A rename is not a re-categorisation.

    Vet and retail bill on different rebate ladders. `\\bVET\\b` alone does not
    match VETERINARY, so the day Royal Canin spells the channel out, the whole
    veterinary range would move onto the retail account's terms.
    """
    for channel in ("VET CAT 獸醫系列 -  貓", "VETERINARY DIET DOG", "Veterinary Health Nutrition",
                    "VET-DOG", "獸醫系列 - 貓"):
        hit = _hit()
        hit["categories"] = {"level0": [channel]}
        assert rc.classify(hit)[0] == rc.RANGE_VET, channel
    for channel in ("PET SHOP CAT 貓糧", "BREED HEALTH NUTRITION", "PUPPY"):
        hit = _hit()
        hit["categories"] = {"level0": [channel]}
        assert rc.classify(hit)[0] == rc.RANGE_NON_VET, channel


def test_a_product_with_no_code_is_dropped_without_a_filing_question():
    """There is nothing to order against, so there is nothing to decide."""
    nameless = _hit(original_sku="", objectID="99")
    nameless["categories"] = {"level0": ["VET CAT 獸醫系列 -  貓", "PET SHOP CAT 貓糧 "]}
    snapshot = rc.build_snapshot([nameless], customer_group=GROUP, captured_on="2026-08-27")

    assert snapshot.row_count == 0
    assert [w.code for w in snapshot.warnings] == [rc.WARN_NO_SUPPLIER_CODE]


def test_the_contract_declares_exactly_the_columns_the_connector_writes():
    """The snapshot's headings are generated, so the contract must track them.

    Both are hand-written literals in different files. Without this, adding a
    column to the connector leaves the contract describing a document that no
    longer exists — and the new column is read by nothing.
    """
    from schemas.catalogue_pipeline.supplier_contracts.suppliers import (
        ROYAL_CANIN_NON_VET_WEBSHOP_SNAPSHOT_V1,
        ROYAL_CANIN_VET_WEBSHOP_SNAPSHOT_V1,
    )

    for contract in (ROYAL_CANIN_VET_WEBSHOP_SNAPSHOT_V1, ROYAL_CANIN_NON_VET_WEBSHOP_SNAPSHOT_V1):
        assert tuple(contract.source_structure.optional_headers) == rc.SNAPSHOT_COLUMNS


def test_the_deepest_category_is_the_deepest_by_number():
    """Sorted as text, "level9" outranks "level10" and the real leaf is lost."""
    hit = _hit()
    hit["categories"] = {"level0": ["VET CAT"], "level9": ["NINE"], "level10": ["DEEPEST"]}
    snapshot = rc.build_snapshot([hit], customer_group=GROUP, captured_on="2026-08-27")

    assert _rows(snapshot)[0]["category_path"] == "DEEPEST"
