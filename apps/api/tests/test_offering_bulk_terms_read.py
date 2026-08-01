"""Published catalogue bulk terms reach the SKU page.

The pipeline writes typed MBB terms against the OFFERING
(`catalogue_supplier_mbb_terms.supplier_product_id`); the hand-entered ones
hang off the legacy link (`mbb_terms.product_supplier_id`). Nothing joined the
two, so publishing Hill's MOV ladder left the SKU page still saying "no bulk
terms". These pin the read that closes that gap — and the arithmetic, because a
bulk price carries a basis exactly like a catalogue price does.
"""

from __future__ import annotations

import os
import tempfile

os.environ.setdefault("DATABASE_URL", f"sqlite:///{tempfile.mkdtemp()}/t.db")

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

import models
from services import offering_costs
from services.pricing_service import _cost_to_hit_mbb, best_mbb, get_unit_cost

NOW = "2026-08-01T00:00:00+00:00"


def _session() -> Session:
    engine = create_engine("sqlite:///:memory:")
    models.Base.metadata.create_all(engine)
    return Session(engine)


def _seed(db: Session, *, unit_price: float = 13.1, packaging: tuple[str, str, float] | None = None):
    """One Hill's link with an offering and a current price."""
    variant = models.ProductVariant(
        sku_code="10006348", name="Hill's Science Plan Adult Chicken 82g", brand="Hill's",
        category="Food", uom="can", storage_rule="any", status="ACTIVE", hero_sku=0,
        created_at=NOW, updated_at=NOW,
    )
    db.add(models.Supplier(id=14, code="HILLS", name="Hill's", created_at=NOW))
    db.add(variant)
    db.flush()
    link = models.ProductSupplier(
        product_id=variant.id, supplier_id=14, supplier_sku="2968", updated_at=NOW,
    )
    db.add(link)
    db.flush()
    offering = models.SupplierOffering(
        supplier_product_key="supplier:14:offer:2968", supplier_id=14,
        product_variant_id=variant.id, supplier_sku="2968", status="active",
        created_at=NOW, updated_at=NOW,
    )
    db.add(offering)
    db.flush()
    if packaging is not None:
        purchase, sellable, per_purchase = packaging
        db.add(models.CataloguePackagingConfiguration(
            supplier_product_id=offering.id, purchase_uom_code=purchase,
            sellable_unit_uom_code=sellable, sellable_units_per_purchase_unit=per_purchase,
            created_at=NOW,
        ))
    db.add(models.CatalogueSupplierPrice(
        supplier_product_id=offering.id, amount=unit_price, currency="HKD",
        price_basis_uom_code=(packaging[0] if packaging else "UNIT"),
        is_current=1, created_at=NOW,
    ))
    db.commit()
    offering_costs.invalidate(db)
    return link, offering


def _add_term(db, offering, **kwargs):
    row = models.CatalogueSupplierMbbTerm(
        supplier_product_id=offering.id, scope=kwargs.pop("scope", "SUPPLIER_ORDER"),
        condition_type=kwargs.pop("condition_type", "minimum_spend"),
        benefit_type=kwargs.pop("benefit_type", "discounted_unit_price"),
        is_active=kwargs.pop("is_active", 1), created_at=NOW, **kwargs,
    )
    db.add(row)
    db.commit()
    offering_costs.invalidate(db)
    return row


def test_a_published_term_is_readable_from_the_supplier_link():
    """The join that did not exist: link -> offering -> published terms."""
    with _session() as db:
        link, offering = _seed(db)
        _add_term(db, offering, condition_spend_amount=1200, condition_spend_currency="HKD",
                  discounted_price_amount=12.4, discounted_price_currency="HKD",
                  discounted_price_basis_uom_code="UNIT",
                  description="Unit price once the order reaches HK$1,200.",
                  ingestion_run_uuid="run-1")

        terms = offering_costs.bulk_terms_for_link(link)
        assert len(terms) == 1
        assert terms[0]["min_spend"] == 1200
        assert terms[0]["effective_unit_cost"] == 12.4
        assert terms[0]["source"] == "catalogue"
        assert terms[0]["scope"] == "SUPPLIER_ORDER"
        assert terms[0]["id"].startswith("catalogue:"), "never mistakable for a legacy term id"


def test_a_bulk_price_quoted_per_case_is_divided_like_any_other_price():
    """A term states its own basis. Quoting the case price per can would read 12x cheap."""
    with _session() as db:
        link, offering = _seed(db, unit_price=157.2, packaging=("CASE", "UNIT", 12))
        _add_term(db, offering, condition_spend_amount=1200, condition_spend_currency="HKD",
                  discounted_price_amount=148.8, discounted_price_currency="HKD",
                  discounted_price_basis_uom_code="CASE")

        assert get_unit_cost(link) == 13.1                       # 157.20 / 12
        assert offering_costs.bulk_terms_for_link(link)[0]["effective_unit_cost"] == 12.4


def test_tiers_read_back_cheapest_last_so_a_ladder_reads_in_order():
    with _session() as db:
        link, offering = _seed(db)
        for spend, price in ((4500, 11.4), (1200, 12.4), (2200, 11.9)):
            _add_term(db, offering, condition_spend_amount=spend, condition_spend_currency="HKD",
                      discounted_price_amount=price, discounted_price_currency="HKD",
                      discounted_price_basis_uom_code="UNIT")

        assert [t["min_spend"] for t in offering_costs.bulk_terms_for_link(link)] == [1200, 2200, 4500]


def test_the_headline_best_bulk_cost_sees_published_terms():
    """Otherwise the summary line contradicts the table directly beneath it."""
    with _session() as db:
        link, offering = _seed(db)
        db.add(models.MbbTerm(product_supplier_id=link.id, kind="tier", min_qty=24,
                              unit_cost=12.8, sort_order=0, created_at=NOW, updated_at=NOW))
        db.commit()
        _add_term(db, offering, condition_spend_amount=4500, condition_spend_currency="HKD",
                  discounted_price_amount=11.4, discounted_price_currency="HKD",
                  discounted_price_basis_uom_code="UNIT")
        db.refresh(link)

        cost, term = best_mbb(link, get_unit_cost(link))
        assert cost == 11.4, "the published term is cheaper than the hand-entered one"
        assert getattr(term, "source", None) == "catalogue"
        # What you must lay out is the threshold itself, not a quantity guess.
        assert _cost_to_hit_mbb(term, get_unit_cost(link), cost) == 4500


def test_a_hand_entered_term_still_wins_when_it_is_cheaper():
    with _session() as db:
        link, offering = _seed(db)
        db.add(models.MbbTerm(product_supplier_id=link.id, kind="tier", min_qty=24,
                              unit_cost=10.0, sort_order=0, created_at=NOW, updated_at=NOW))
        db.commit()
        _add_term(db, offering, condition_spend_amount=1200, condition_spend_currency="HKD",
                  discounted_price_amount=12.4, discounted_price_currency="HKD",
                  discounted_price_basis_uom_code="UNIT")
        db.refresh(link)

        cost, term = best_mbb(link, get_unit_cost(link))
        assert cost == 10.0
        assert getattr(term, "source", None) is None


def test_a_free_quantity_term_prices_the_units_you_take_home():
    with _session() as db:
        link, offering = _seed(db, unit_price=12.0)
        _add_term(db, offering, scope="SUPPLIER_SKU", condition_type="minimum_quantity",
                  condition_quantity_amount=5, condition_quantity_uom_code="UNIT",
                  benefit_type="free_quantity", free_quantity_amount=1,
                  free_quantity_uom_code="UNIT")

        # Pay for 5, take 6 home: 60 / 6 = 10.
        assert offering_costs.bulk_terms_for_link(link)[0]["effective_unit_cost"] == 10.0


def test_a_percentage_term_comes_off_the_current_price():
    with _session() as db:
        link, offering = _seed(db, unit_price=20.0)
        _add_term(db, offering, benefit_type="percentage_discount", percentage_discount=15,
                  condition_spend_amount=1000, condition_spend_currency="HKD")

        assert offering_costs.bulk_terms_for_link(link)[0]["effective_unit_cost"] == 17.0


def test_withdrawn_and_superseded_terms_are_not_shown():
    """A term the supplier withdrew must not keep quoting a price."""
    with _session() as db:
        link, offering = _seed(db)
        _add_term(db, offering, condition_spend_amount=1200, condition_spend_currency="HKD",
                  discounted_price_amount=12.4, discounted_price_currency="HKD",
                  discounted_price_basis_uom_code="UNIT", is_active=0)
        superseded = _add_term(db, offering, condition_spend_amount=2200, condition_spend_currency="HKD",
                               discounted_price_amount=11.9, discounted_price_currency="HKD",
                               discounted_price_basis_uom_code="UNIT")
        superseded.superseded_at = NOW
        db.commit()
        offering_costs.invalidate(db)

        assert offering_costs.bulk_terms_for_link(link) == []


def test_the_scanned_file_rides_along_so_a_price_can_be_traced():
    with _session() as db:
        link, offering = _seed(db)
        source = models.CatalogueSourceDocument(
            supplier_id=14, filename="hills_classic.pdf", source_format="PDF",
            source_ref="v2/x.pdf", source_checksum="abc", received_at="2026-07-28T00:00:00+00:00",
            supplier_source_contract_id="hills.price_list.v1", supplier_source_contract_version="v1",
            document_type="PRICE_LIST", byte_size=10, page_count=1, created_at=NOW,
        )
        db.add(source)
        db.flush()
        legacy = models.CatalogueImport(supplier_id=14, filename="hills_classic.pdf", imported_at=NOW)
        db.add(legacy)
        db.flush()
        db.add(models.IngestionRun(
            run_uuid="run-9", source_document_id=legacy.id,
            catalogue_source_document_id=source.id, supplier_id=14,
            contract_version="catalogue.extraction_profile.v1",
            supplier_source_contract_id="hills.price_list.v1", supplier_source_contract_version="v1",
            document_type="PRICE_LIST", extractor_name="queued-submission", extractor_version="v1",
            status="completed", created_at=NOW,
        ))
        db.commit()
        _add_term(db, offering, condition_spend_amount=1200, condition_spend_currency="HKD",
                  discounted_price_amount=12.4, discounted_price_currency="HKD",
                  discounted_price_basis_uom_code="UNIT", ingestion_run_uuid="run-9")

        term = offering_costs.bulk_terms_for_link(link)[0]
        assert term["source_file"] == "hills_classic.pdf"
        assert term["run_id"] == "run-9"


def test_a_link_with_no_offering_reads_no_terms():
    with _session() as db:
        variant = models.ProductVariant(
            sku_code="RIMS-NO-OFFER", name="Loose item", category="Food", uom="unit",
            storage_rule="any", status="ACTIVE", hero_sku=0, created_at=NOW, updated_at=NOW,
        )
        db.add(models.Supplier(id=21, code="ALF", name="Alfamedic", created_at=NOW))
        db.add(variant)
        db.flush()
        link = models.ProductSupplier(product_id=variant.id, supplier_id=21, updated_at=NOW)
        db.add(link)
        db.commit()

        assert offering_costs.bulk_terms_for_link(link) == []
    # Detached duck-typed stand-ins carry none either.
    assert offering_costs.bulk_terms_for_link(models.ProductSupplier(units_per_pack=4)) == []


if __name__ == "__main__":
    import pytest

    raise SystemExit(pytest.main([__file__, "-q"]))
