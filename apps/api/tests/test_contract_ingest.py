"""DC-2 — contract-first ingestion wiring (deterministic; extraction is monkeypatched, no LLM).

Verifies: a contracted supplier's upload → the contract guides extraction (asserted via the passed-in
contract) and enforces invariants + flags validation failures; an uncontracted supplier → generic path,
untouched.
"""
import os
import tempfile

os.environ.setdefault("DATABASE_URL", f"sqlite:///{tempfile.mkdtemp()}/t.db")

import pytest        # noqa: E402
import database      # noqa: E402
import models        # noqa: E402
import main          # noqa: E402
from fastapi.testclient import TestClient          # noqa: E402
from dependencies import require_user               # noqa: E402
from services import extraction_service, tagging_service, catalogue_contract  # noqa: E402

models.Base.metadata.create_all(bind=database.engine)
database.run_migrations(database.engine)


class _Admin:
    id, username, display_name, role = 9, "onboarder", "On Boarder", "admin"


@pytest.fixture(autouse=True)
def _setup(monkeypatch):
    prev = main.app.dependency_overrides.get(require_user)
    main.app.dependency_overrides[require_user] = lambda: _Admin()
    monkeypatch.setattr(tagging_service, "suggest_tags",
                        lambda items: [{"tags": [], "category": None, "subcategory": None} for _ in items])
    catalogue_contract.reload_contracts()
    yield
    if prev is None:
        main.app.dependency_overrides.pop(require_user, None)
    else:
        main.app.dependency_overrides[require_user] = prev


_client = TestClient(main.app)


def _supplier(sid, code, name):
    d = database.SessionLocal()
    try:
        if not d.get(models.Supplier, sid):
            d.add(models.Supplier(id=sid, code=code, name=name, created_at="2026-01-01"))
            d.commit()
    finally:
        d.close()


def _items(import_id):
    d = database.SessionLocal()
    try:
        return {it.supplier_sku: it for it in d.query(models.CatalogueItem).filter_by(import_id=import_id).all()}
    finally:
        d.close()


def test_contracted_import_guides_enforces_and_flags(monkeypatch):
    _supplier(14, "HILLS", "Hill's")

    def fake_extract(content, filename, content_type, contract=None):
        assert contract is not None and contract.supplier_id == 14      # contract-first is wired
        return ([
            {"supplier_sku": "10447", "description": "Healthy Cuisine", "cost_price": 13.10, "rrp": 18.0,
             "units_per_pack": 24, "order_increment_qty": 24, "brand": None},          # good row
            {"supplier_sku": "SWAP1", "description": "Swapped row", "cost_price": 25.0, "rrp": 17.6,
             "units_per_pack": 24, "order_increment_qty": 24, "brand": None},          # cost > rrp → flag
        ], "pdf")
    monkeypatch.setattr(extraction_service, "extract", fake_extract)

    r = _client.post("/catalogues/import", data={"supplier_id": "14"},
                     files={"file": ("hills.pdf", b"%PDF-1.4 fake", "application/pdf")})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["contract"] and "hills" in body["contract"]
    assert body["contract_flags"] == 1

    it = _items(body["import_id"])
    # enforced invariants on the good row
    assert it["10447"].units_per_pack == 1                 # per-unit — carton (24) NOT the divisor
    assert it["10447"].brand == "Hill's"                   # const column
    assert it["10447"].ai_category == "Food"               # contract category wins over the AI guess
    assert it["10447"].confidence_detail is None           # passes validation
    # the swap is flagged, not silently ingested
    assert it["SWAP1"].confidence_detail and "cost_price < rrp" in it["SWAP1"].confidence_detail


def test_dc3_derive_applies_contract_backfill():
    # DC-3: re-parse re-derives a contracted supplier's row THROUGH its contract — a Hill's row whose pack
    # was mis-set to the carton (24) is corrected to per-unit (1); consts applied.
    from services import reparse_service
    catalogue_contract.reload_contracts()
    item = models.CatalogueItem(supplier_id=14, raw_description="Hill's Can 2.8oz", pack_size="2.8oz",
                                uom="can", units_per_pack=24, cost_price=13.1, rrp=18.0,
                                supplier_sku="10447", species="cat")
    out = reparse_service.derive(item)
    assert out["units_per_pack"] == 1          # contract per-unit invariant overrides the generic guard
    assert out["brand"] == "Hill's"            # const column
    assert out["category"] == "Food"           # const


def test_dc3_derive_unchanged_for_uncontracted():
    from services import reparse_service
    item = models.CatalogueItem(supplier_id=77, raw_description="Widget", pack_size="1", uom="unit",
                                units_per_pack=5, cost_price=10.0, brand="Acme")
    out = reparse_service.derive(item)
    assert out.get("brand") == "Acme"          # no contract → item's own value, not a forced const


def test_dc3_derive_fills_weight_from_size_for_hills():
    # Hill's re-parse re-derives the sell-unit weight from the retained Size string (an oz size the
    # base extraction missed) — deterministically, no model call.
    from services import reparse_service
    catalogue_contract.reload_contracts()
    item = models.CatalogueItem(supplier_id=14, raw_description="Hill's k/d 2.9oz", pack_size="24/2.9 oz",
                                uom="can", units_per_pack=24, cost_price=13.1, rrp=18.0,
                                supplier_sku="10447", weight_grams=None)
    out = reparse_service.derive(item)
    assert out["weight_grams"] == round(2.9 * 28.3495)         # 82 — from Size, sell-unit not the case


def test_reparse_contracted_bypasses_manual_cost_and_pack_gates():
    # Fix: for a contracted supplier the contract cost/pack are authoritative — re-parse proposes them
    # even over a manual/verified live value. Uncontracted stays protected.
    from types import SimpleNamespace
    from services import reparse_service as rp
    ps = SimpleNamespace(cost_source="manual", pack_source="manual", units_per_pack=1,
                         order_increment_qty=None, minimum_order_qty=None, uom_verified_at=None)
    clean = SimpleNamespace(cost_price=13.1, rrp=18.0)        # cost < rrp — a clean contract row
    cand = {"cost_price": 13.1, "units_per_pack": 1}
    # uncontracted → manual cost/pack protected → live value kept
    assert rp._candidate(cand, "cost_price", True, 25.0, ps, clean, contracted=False) == 25.0
    assert rp._candidate(cand, "units_per_pack", True, 24, ps, clean, contracted=False) == 24
    # contracted → contract value flows through
    assert rp._candidate(cand, "cost_price", True, 25.0, ps, clean, contracted=True) == 13.1
    assert rp._candidate(cand, "units_per_pack", True, 24, ps, clean, contracted=True) == 1


def test_reparse_swap_guard_holds_even_when_contracted():
    # The cost>rrp swap guard is a correctness check, not a provenance one — it holds even for a
    # contracted supplier, so a swapped row is never pushed onto the live SKU.
    from types import SimpleNamespace
    from services import reparse_service as rp
    ps = SimpleNamespace(cost_source="manual", pack_source="manual", units_per_pack=1,
                         order_increment_qty=None, minimum_order_qty=None, uom_verified_at=None)
    swapped = SimpleNamespace(cost_price=25.0, rrp=17.6)      # cost > rrp → columns swapped
    cand = {"cost_price": 25.0}
    assert rp._candidate(cand, "cost_price", True, 16.7, ps, swapped, contracted=True) == 16.7  # not applied


def test_dc4_drift_flags_stale_contract(monkeypatch):
    # DC-4: a restyled catalogue where the columns no longer match → mass validation failure → contract_stale
    _supplier(14, "HILLS", "Hill's")

    def fake_extract(content, filename, content_type, contract=None):
        return ([{"supplier_sku": f"S{i}", "description": "x", "cost_price": 100.0, "rrp": 50.0,
                  "units_per_pack": 1, "order_increment_qty": 1} for i in range(6)], "pdf")   # every row cost>rrp
    monkeypatch.setattr(extraction_service, "extract", fake_extract)

    r = _client.post("/catalogues/import", data={"supplier_id": "14"},
                     files={"file": ("hills.pdf", b"%PDF", "application/pdf")})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["contract_flags"] == 6 and body["contract_stale"] is True


def test_uncontracted_import_is_unchanged(monkeypatch):
    _supplier(77, "GEN", "Generic Supplier")

    def fake_extract(content, filename, content_type, contract=None):
        assert contract is None                            # no contract → generic path
        return ([{"supplier_sku": "G1", "description": "Widget", "cost_price": 10.0, "units_per_pack": 5}], "pdf")
    monkeypatch.setattr(extraction_service, "extract", fake_extract)

    r = _client.post("/catalogues/import", data={"supplier_id": "77"},
                     files={"file": ("g.pdf", b"%PDF fake", "application/pdf")})
    assert r.status_code == 200, r.text
    assert r.json()["contract"] is None and r.json()["contract_flags"] == 0
    assert _items(r.json()["import_id"])["G1"].units_per_pack == 5    # untouched — no enforcement
