"""Catalogue re-parse endpoints (RP-2.4 / RP-4.1). Stage a diff, review it, confirm it.

  POST /catalogues/reparse/{scope}/{ref}    scope ∈ item|import|supplier → create a batch, return the diff
  GET  /catalogues/reparse/{batch_id}       the batch + its changes
  POST /catalogues/reparse/{batch_id}/confirm  body {change_ids:[...]} (empty = all pending) → apply, guarded
  POST /catalogues/reparse/{batch_id}/discard  drop the batch (no writes)

The POST/GET/confirm endpoints only stage or apply CONFIRMED changes; re-parse never writes
Product/ProductSupplier except through a confirmed change (which re-verifies the live value first).
"""
import re
from datetime import datetime, timezone
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

import database
import models
from permissions import require_capability
from services import reparse_service, audit_log

router = APIRouter(prefix="/catalogues/reparse", tags=["catalogue-reparse"])


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _change_dict(db: Session, c: models.ReparseChange) -> dict:
    item = db.get(models.CatalogueItem, c.catalogue_item_id)
    sku, name = reparse_service._sku_and_name(db, item) if item else (None, "")
    import_id = item.import_id if item else None
    source_file = None
    if import_id:
        imp = db.get(models.CatalogueImport, import_id)
        source_file = imp.filename if imp else None
    return {
        "id": c.id, "catalogue_item_id": c.catalogue_item_id, "product_id": c.product_id,
        "committed": bool(c.product_id), "sku_code": sku, "product_name": name,
        "import_id": import_id, "source_file": source_file,
        "field": c.field, "old_value": c.old_value, "new_value": c.new_value,
        "affects_cost": bool(c.affects_cost), "eff_cost_before": c.eff_cost_before,
        "eff_cost_after": c.eff_cost_after, "status": c.status,
    }


def _source_file(db: Session, item) -> tuple:
    import_id = item.import_id if item else None
    if import_id:
        imp = db.get(models.CatalogueImport, import_id)
        return import_id, (imp.filename if imp else None)
    return import_id, None


def _item_card(db: Session, batch: models.ReparseBatch, item: models.CatalogueItem) -> dict:
    """One item's review card: every display field (Current vs Re-parsed), each staged change overlaid
    with its status + change_id. Shared by the batch list and the single-item edit response so they render
    identically."""
    changes = (db.query(models.ReparseChange)
               .filter(models.ReparseChange.batch_id == batch.id,
                       models.ReparseChange.catalogue_item_id == item.id)
               .order_by(models.ReparseChange.id).all())
    chg_by_field = {c.field: c for c in changes}
    sku, name = reparse_service._sku_and_name(db, item)
    import_id, source_file = _source_file(db, item)
    fields = reparse_service.item_snapshot(db, item)
    for row in fields:
        c = chg_by_field.get(row["field"])
        if c is not None:      # authoritative: use the staged change (old/new/eff) + its status
            row.update(change_id=c.id, status=c.status, changed=True,
                       current=c.old_value, reparsed=c.new_value,
                       eff_cost_before=c.eff_cost_before, eff_cost_after=c.eff_cost_after)
        else:
            # no staged change → NOT actionable, even if the raw snapshot differed (e.g. an
            # unresolved supplier link). Force changed=False so it can't show as a phantom
            # "pending" the reviewer can never confirm ("Nothing to confirm").
            row.update(change_id=None, status=None, changed=False, reparsed=row["current"])
    return {
        "catalogue_item_id": item.id, "product_id": item.matched_product_id,
        "committed": bool(item.matched_product_id), "sku_code": sku, "product_name": name,
        "import_id": import_id, "source_file": source_file,
        "changed_count": len(changes),
        "change_ids": [c.id for c in changes if c.status == "pending"],
        "fields": fields,
    }


def _items_payload(db: Session, batch: models.ReparseBatch) -> list:
    """Per-item cards, in change-id order. Only items that HAVE a change appear (a no-change item produces
    no batch rows)."""
    changes = (db.query(models.ReparseChange)
               .filter(models.ReparseChange.batch_id == batch.id)
               .order_by(models.ReparseChange.id).all())
    order, seen = [], set()
    for c in changes:
        if c.catalogue_item_id not in seen:
            seen.add(c.catalogue_item_id)
            order.append(c.catalogue_item_id)
    items = []
    for iid in order:
        item = db.get(models.CatalogueItem, iid)
        if item is not None:
            items.append(_item_card(db, batch, item))
    return items


def _supplier_name(db: Session, batch: models.ReparseBatch) -> Optional[str]:
    """Human name for a supplier-scoped batch (scope_ref is the supplier id) so the review header
    reads 'Royal Canin' instead of 'Supplier #51'. None for item/import scope (those label off the
    product name / source file)."""
    if batch.scope_type != "supplier":
        return None
    try:
        sup = db.get(models.Supplier, int(batch.scope_ref))
    except (TypeError, ValueError):
        return None
    return sup.name if sup else None


def _batch_dict(db: Session, batch: models.ReparseBatch) -> dict:
    changes = (db.query(models.ReparseChange)
               .filter(models.ReparseChange.batch_id == batch.id)
               .order_by(models.ReparseChange.id).all())
    return {
        "id": batch.id, "scope_type": batch.scope_type, "scope_ref": batch.scope_ref,
        "supplier_name": _supplier_name(db, batch),          # named supplier for supplier-scoped batches
        "parser_version": batch.parser_version, "mode": batch.mode, "status": batch.status,
        "item_count": batch.item_count, "changed_count": batch.changed_count,
        "created_at": batch.created_at,
        "changes": [_change_dict(db, c) for c in changes],   # flat (confirm/back-compat)
        "items": _items_payload(db, batch),                  # per-item cards (all display fields)
    }


def _resolve_items(db: Session, scope: str, ref: str) -> List[models.CatalogueItem]:
    q = db.query(models.CatalogueItem)
    if scope == "item":
        product = db.query(models.Product).filter(models.Product.sku_code == ref).first()
        if not product:
            raise HTTPException(status_code=404, detail=f"No product with SKU {ref}")
        return q.filter(models.CatalogueItem.matched_product_id == product.id).all()
    if scope == "import":
        return q.filter(models.CatalogueItem.import_id == _int(ref, "import id")).all()
    if scope == "supplier":
        return q.filter(models.CatalogueItem.supplier_id == _int(ref, "supplier id")).all()
    raise HTTPException(status_code=400, detail="scope must be item | import | supplier")


def _int(v, what):
    try:
        return int(v)
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail=f"{what} must be an integer")


# ── Name corroboration (used to trust a single-row match whose supplier_sku differs from the live link) ──
_NAME_STOP = {
    "hills", "hill", "prescription", "diet", "science", "plan", "vet", "essentials",
    "dry", "wet", "canned", "can", "cans", "pouch", "pouches", "bag", "bags", "tray", "trays",
    "food", "kibble", "stew", "loaf", "mousse", "chunks", "gravy", "minced", "sliced",
    "cat", "cats", "dog", "dogs", "canine", "feline", "kitten", "puppy",
    "adult", "senior", "mature", "junior", "small", "large", "medium", "mini", "breed",
    "the", "and", "with", "for", "plus", "formula", "new", "min", "no", "of", "bot", "btl", "bottle",
}
_JOIN_SIZE = re.compile(r"(\d)\s+(kg|kgs|g|gm|lb|lbs|oz|ml|l)\b")
_SIZE_TOK = re.compile(r"^(\d+(?:\.\d+)?)(kg|kgs|g|gm|lb|lbs|oz|ml|l)$")
_SIZE_UNIT = {"kgs": "kg", "lbs": "lb", "gm": "g"}


def _disc(name):
    """(identifying words, normalized pack sizes) for a product name. Words carry the discriminating identity
    (therapeutic code like c/d, organ/condition, flavour); sizes are compared softly (formats vary and one
    side often omits them). Common brand/form/species/life-stage words are dropped."""
    s = (name or "").lower()
    s = _JOIN_SIZE.sub(r"\1\2", s).replace("/bot", " ").replace("/btl", " ")
    s = re.sub(r"[^a-z0-9/%.]+", " ", s)                    # keep / (c/d), % (2%), . (1.5)
    words, sizes = set(), set()
    for t in s.split():
        t = t.strip(".")
        if not t or t in _NAME_STOP:
            continue
        if t[0].isdigit():                                 # a pack size, a dose/concentration, or a bare count
            m = _SIZE_TOK.match(t)
            if m:
                sizes.add(m.group(1) + _SIZE_UNIT.get(m.group(2), m.group(2)))
            # else (2%, 20mg/ml, a bare number) → inconsistently mentioned, not an identity signal → ignore
            continue
        if len(t) >= 2 or "/" in t:                        # a word, incl. a therapeutic code like c/d
            words.add(t)
    return words, sizes


def _names_match(product_name, row_desc) -> bool:
    """Same product? Identifying words must be EQUAL; sizes must be compatible (equal, or absent on a side).
    Conservative: any altered/extra identifying word (c/d vs u/d, 'i/d' vs 'i/d low fat') fails."""
    wa, sa = _disc(product_name)
    wb, sb = _disc(row_desc)
    if not wa or wa != wb:
        return False
    return (not sa) or (not sb) or (sa == sb)


def _trusted_committed_row(db: Session, product_id, supplier_id, grp_newest_first):
    """The ONE catalogue row we trust as this product's own — or None when the match is too ambiguous to
    recapture from safely. A product can end up over-matched with SEVERAL other SKUs' catalogue rows (a
    known Hill's issue); re-parsing those would overwrite a good live SKU with another product's
    cost/identity. Rules, in order:
      1. prefer a matched row whose supplier_sku == the product's live supplier link (its own row);
      2. else, a SINGLE-identity match (≤1 distinct supplier_sku). If it would change a NON-EMPTY live
         supplier_sku, trust it only when the row's NAME corroborates the product (a genuine stale-sku fix,
         e.g. Lignocaine); a single mis-matched row whose name doesn't match is rejected. An empty live
         sku is simply filled;
      3. else (several different supplier_skus, none matching the live link) → None: don't recapture."""
    ps = (db.query(models.ProductSupplier)
          .filter(models.ProductSupplier.product_id == product_id,
                  models.ProductSupplier.supplier_id == supplier_id).first())
    live_sku = str(ps.supplier_sku).strip() if ps and ps.supplier_sku else None
    if live_sku:
        for it in grp_newest_first:                        # newest-first → newest own-row wins
            if (it.supplier_sku or "").strip() == live_sku:
                return it                                  # rule 1: its own row (sku matches the live link)
    distinct = {(it.supplier_sku or "").strip() for it in grp_newest_first if (it.supplier_sku or "").strip()}
    if len(distinct) <= 1:
        row = grp_newest_first[0]
        row_sku = (row.supplier_sku or "").strip()
        if live_sku and row_sku and row_sku != live_sku:   # would change a real live sku → corroborate by name
            prod = db.get(models.Product, product_id)
            if not _names_match(prod.name if prod else None, row.raw_description):
                return None                                # single mis-matched row — don't overwrite the live SKU
        return row
    return None


def _dedupe_latest(db: Session, items: List[models.CatalogueItem]):
    """Select the ONE catalogue row to re-parse per SKU. Returns (rows, ambiguous_dropped).

    Committed products: the trusted own-row (see _trusted_committed_row). A product whose matched rows are
    an ambiguous mix of several supplier_skus (over-matched) is DROPPED — re-parse must not overwrite its
    live cost/identity with another SKU's data. Pending items: the most recent row per supplier_sku, as
    before. 'Most recent' = latest created_at, then import, then id."""
    def _recency(it):
        return (it.created_at or "", it.import_id or 0, it.id or 0)

    groups = {}
    for it in items:
        if it.matched_product_id:
            k = ("product", it.matched_product_id, it.supplier_id)
        else:
            sku = (it.supplier_sku or "").strip().lower()
            k = ("sku", it.supplier_id, sku) if sku else ("item", it.id)
        groups.setdefault(k, []).append(it)

    chosen, ambiguous = [], 0
    for k, grp in groups.items():
        grp.sort(key=_recency, reverse=True)
        if k[0] == "product":
            pick = _trusted_committed_row(db, k[1], k[2], grp)
            if pick is not None:
                chosen.append(pick)
            else:
                ambiguous += 1                             # over-matched SKU — skip, don't corrupt it
        else:
            chosen.append(grp[0])
    return sorted(chosen, key=_recency, reverse=True), ambiguous


def _supersede_prior(db: Session, new_batch: models.ReparseBatch, item_ids, operator) -> int:
    """Newest re-parse of a SKU wins. Any PENDING change in an OTHER open batch for one of these
    catalogue_items is superseded, so a SKU never carries two live re-parses. An open batch left with
    no pending changes is closed ('superseded'). Confirmed changes (already written to live cost) are
    never touched, and other suppliers' open re-parses are left alone — they stay resumable.
    Returns how many changes were superseded."""
    ids = list({i for i in item_ids if i is not None})
    if not ids:
        return 0
    prior = []
    for i in range(0, len(ids), 500):                      # chunk to stay under the SQLite param cap
        chunk = ids[i:i + 500]
        prior += (db.query(models.ReparseChange)
                  .join(models.ReparseBatch, models.ReparseChange.batch_id == models.ReparseBatch.id)
                  .filter(models.ReparseChange.batch_id != new_batch.id,
                          models.ReparseChange.status == "pending",
                          models.ReparseBatch.status == "open",
                          models.ReparseChange.catalogue_item_id.in_(chunk))
                  .all())
    if not prior:
        return 0
    affected = set()
    for ch in prior:
        ch.status = "superseded"
        affected.add(ch.batch_id)
    db.flush()                                             # so the pending re-count below sees the flip
    closed = 0
    for bid in affected:
        remaining = (db.query(models.ReparseChange)
                     .filter(models.ReparseChange.batch_id == bid,
                             models.ReparseChange.status == "pending").count())
        if remaining == 0:
            b = db.get(models.ReparseBatch, bid)
            if b and b.status == "open":
                b.status = "superseded"
                closed += 1
    audit_log.record(db, action="catalogue.reparse_supersede", actor=None, entity_type="reparse_batch",
                     entity_id=new_batch.id, entity_label=f"{new_batch.scope_type}:{new_batch.scope_ref}",
                     details={"superseded_changes": len(prior), "closed_batches": closed, "operator": operator})
    return len(prior)


def _batch_supplier(db: Session, batch: models.ReparseBatch):
    """(supplier_id, supplier_name) for a batch — from scope_ref for supplier scope, else from the
    supplier of its catalogue items (import/item scope)."""
    if batch.scope_type == "supplier":
        try:
            sid = int(batch.scope_ref)
        except (TypeError, ValueError):
            return None, None
        sup = db.get(models.Supplier, sid)
        return sid, (sup.name if sup else None)
    ch = db.query(models.ReparseChange).filter(models.ReparseChange.batch_id == batch.id).first()
    if ch:
        item = db.get(models.CatalogueItem, ch.catalogue_item_id)
        if item and item.supplier_id:
            sup = db.get(models.Supplier, item.supplier_id)
            return item.supplier_id, (sup.name if sup else None)
    return None, None


def _open_batch_row(db: Session, batch: models.ReparseBatch) -> dict:
    """Inbox row for an open batch: supplier + a human title + live pending counts (not the frozen
    changed_count, which drifts as changes confirm / get superseded)."""
    sid, sname = _batch_supplier(db, batch)
    pending = (db.query(models.ReparseChange)
               .filter(models.ReparseChange.batch_id == batch.id, models.ReparseChange.status == "pending"))
    pcount = pending.count()
    pitems = pending.with_entities(models.ReparseChange.catalogue_item_id).distinct().count()
    title = sname
    if batch.scope_type != "supplier":
        first = db.query(models.ReparseChange).filter_by(batch_id=batch.id).order_by(models.ReparseChange.id).first()
        item = db.get(models.CatalogueItem, first.catalogue_item_id) if first else None
        if item is not None:
            if batch.scope_type == "item":
                title = reparse_service._sku_and_name(db, item)[1]
            else:
                title = _source_file(db, item)[1] or sname
    return {
        "id": batch.id, "scope_type": batch.scope_type, "scope_ref": batch.scope_ref,
        "supplier_id": sid, "supplier_name": sname, "title": title,
        "parser_version": batch.parser_version, "created_at": batch.created_at,
        "changed_count": pcount, "pending_items": pitems,
    }


def _search_open_items(db: Session, q: str, supplier=None) -> list:
    """Search re-parsed SKUs (items with a pending change in an open batch) by sku / name / brand /
    category / description, optionally within one supplier. Returns hits with their batch_id so the UI
    can jump straight to that SKU in its review."""
    needle = q.strip().lower()
    if not needle:
        return []
    rows = (db.query(models.ReparseChange.catalogue_item_id, models.ReparseChange.batch_id)
            .join(models.ReparseBatch, models.ReparseChange.batch_id == models.ReparseBatch.id)
            .filter(models.ReparseBatch.status == "open", models.ReparseChange.status == "pending").all())
    batch_of, count_of = {}, {}
    for cid, bid in rows:
        batch_of.setdefault(cid, bid)
        count_of[cid] = count_of.get(cid, 0) + 1
    hits = []
    for cid, bid in batch_of.items():
        item = db.get(models.CatalogueItem, cid)
        if item is None or (supplier is not None and item.supplier_id != supplier):
            continue
        sku, name = reparse_service._sku_and_name(db, item)
        brand = category = None
        if item.matched_product_id:
            p = db.get(models.Product, item.matched_product_id)
            if p:
                brand, category = p.brand, p.category
        hay = " ".join(str(x) for x in (sku, name, brand, category, item.raw_description, item.supplier_sku) if x).lower()
        if needle not in hay:
            continue
        sup = db.get(models.Supplier, item.supplier_id) if item.supplier_id else None
        hits.append({
            "catalogue_item_id": cid, "batch_id": bid, "sku_code": sku, "product_name": name,
            "supplier_id": item.supplier_id, "supplier_name": sup.name if sup else None,
            "changed_count": count_of.get(cid, 0),
        })
    hits.sort(key=lambda h: (h["supplier_name"] or "", h["sku_code"] or ""))
    return hits[:50]


# The 'Re-parse' nav entry resolves here → the current / most-recent batch. Registered before /{batch_id}
# so "latest" isn't parsed as a batch id.
@router.get("/latest")
def latest_reparse(db: Session = Depends(database.get_db),
                   _user: models.User = Depends(require_capability("catalogue_onboard"))):
    """Most recent re-parse batch — prefers the open (in-progress) one, else the latest overall;
    {batch: null} when there has never been one."""
    batch = (db.query(models.ReparseBatch).filter(models.ReparseBatch.status == "open")
             .order_by(models.ReparseBatch.id.desc()).first()
             or db.query(models.ReparseBatch).order_by(models.ReparseBatch.id.desc()).first())
    return {"batch": _batch_dict(db, batch) if batch else None}


# The re-parse inbox: every in-progress (open) re-parse, resumable — not just the latest. `supplier`
# narrows the list; `q` searches re-parsed SKUs across all open re-parses. Registered before /{batch_id}.
@router.get("/open")
def open_reparses(supplier: Optional[int] = None, q: Optional[str] = None,
                  db: Session = Depends(database.get_db),
                  _user: models.User = Depends(require_capability("catalogue_onboard"))):
    open_batches = (db.query(models.ReparseBatch)
                    .filter(models.ReparseBatch.status == "open")
                    .order_by(models.ReparseBatch.id.desc()).all())
    rows = []
    for b in open_batches:
        row = _open_batch_row(db, b)
        if supplier is not None and row["supplier_id"] != supplier:
            continue
        if row["changed_count"] == 0:            # nothing left to review — don't surface it
            continue
        rows.append(row)
    items = _search_open_items(db, q, supplier) if q and q.strip() else []
    return {"batches": rows, "items": items}


# NOTE: the batch routes below (GET /{batch_id}, POST /{batch_id}/confirm|discard) MUST be registered
# before the greedy create route `POST /{scope}/{ref:path}` (defined last) — otherwise create would
# swallow `/5/confirm` as scope="5", ref="confirm".
@router.get("/{batch_id}")
def get_reparse(batch_id: int, db: Session = Depends(database.get_db),
                _user: models.User = Depends(require_capability("catalogue_onboard"))):
    batch = db.get(models.ReparseBatch, batch_id)
    if not batch:
        raise HTTPException(status_code=404, detail="Batch not found")
    return _batch_dict(db, batch)


class ConfirmBody(BaseModel):
    change_ids: Optional[List[int]] = None   # None / [] = all pending in the batch


@router.post("/{batch_id}/confirm")
def confirm_reparse(batch_id: int, body: ConfirmBody = ConfirmBody(),
                    db: Session = Depends(database.get_db),
                    user: models.User = Depends(require_capability("catalogue_onboard"))):
    """Apply the confirmed changes. Each is re-verified against the live value; a drifted row is skipped
    ('stale'), never overwritten. Cost-affecting writes go to the correct supplier link, audited."""
    batch = db.get(models.ReparseBatch, batch_id)
    if not batch:
        raise HTTPException(status_code=404, detail="Batch not found")
    q = (db.query(models.ReparseChange)
         .filter(models.ReparseChange.batch_id == batch_id,
                 models.ReparseChange.status == "pending"))
    if body.change_ids:
        q = q.filter(models.ReparseChange.id.in_(body.change_ids))
    operator = getattr(user, "username", None)
    applied = skipped = 0
    for change in q.all():
        result = reparse_service.apply_change(db, change, operator)
        if result == "confirmed":
            applied += 1
        else:
            skipped += 1
    # batch is 'applied' once no pending changes remain (flush first — the session may not autoflush)
    db.flush()
    remaining = (db.query(models.ReparseChange)
                 .filter(models.ReparseChange.batch_id == batch_id,
                         models.ReparseChange.status == "pending").count())
    if remaining == 0:
        batch.status = "applied"
    db.commit()
    out = _batch_dict(db, batch)
    out["applied"] = applied
    out["skipped"] = skipped
    return out


class FieldEditBody(BaseModel):
    catalogue_item_id: int
    field: str
    value: Optional[str] = None   # the new value to save; "" / null clears the field


@router.put("/{batch_id}/field")
def edit_reparse_field(batch_id: int, body: FieldEditBody,
                       db: Session = Depends(database.get_db),
                       user: models.User = Depends(require_capability("catalogue_onboard"))):
    """Hand-set the value re-parse will save for one field on one in-review SKU, before confirm. Upserts
    (or, when it equals the live value, clears) the pending change for (item, field). No live write here —
    confirm still applies it, re-verifying the live value first. Returns the refreshed item card."""
    batch = db.get(models.ReparseBatch, batch_id)
    if not batch:
        raise HTTPException(status_code=404, detail="Batch not found")
    if batch.status != "open":
        raise HTTPException(status_code=400, detail="This re-parse is closed — re-run it to make edits.")
    item = db.get(models.CatalogueItem, body.catalogue_item_id)
    if not item:
        raise HTTPException(status_code=404, detail="Item not found")
    in_batch = (db.query(models.ReparseChange)
                .filter(models.ReparseChange.batch_id == batch.id,
                        models.ReparseChange.catalogue_item_id == item.id).first())
    if not in_batch:
        raise HTTPException(status_code=400, detail="This SKU isn't part of this re-parse")
    try:
        ch = reparse_service.set_field_value(db, batch, item, body.field, body.value)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    batch.changed_count = (db.query(models.ReparseChange)
                           .filter(models.ReparseChange.batch_id == batch.id).count())
    audit_log.record(db, action="catalogue.reparse_edit", actor=user, entity_type="reparse_batch",
                     entity_id=batch.id, entity_label=reparse_service._sku_and_name(db, item)[0],
                     details={"field": body.field, "value": body.value, "catalogue_item_id": item.id,
                              "result": "cleared" if ch is None else "set"})
    db.commit()
    return {"item": _item_card(db, batch, item), "changed_count": batch.changed_count}


@router.post("/{batch_id}/discard")
def discard_reparse(batch_id: int, db: Session = Depends(database.get_db),
                    user: models.User = Depends(require_capability("catalogue_onboard"))):
    batch = db.get(models.ReparseBatch, batch_id)
    if not batch:
        raise HTTPException(status_code=404, detail="Batch not found")
    batch.status = "discarded"
    (db.query(models.ReparseChange)
     .filter(models.ReparseChange.batch_id == batch_id, models.ReparseChange.status == "pending")
     .update({models.ReparseChange.status: "rejected"}, synchronize_session=False))
    audit_log.record(db, action="catalogue.reparse_discard", actor=user, entity_type="reparse_batch",
                     entity_id=batch.id, entity_label=str(batch_id),
                     details={"scope": batch.scope_type, "ref": batch.scope_ref})
    db.commit()
    return {"ok": True}


# ── Create (registered LAST so its greedy `{scope}/{ref:path}` can't shadow the batch routes above) ──
@router.post("/{scope}/{ref:path}")
def create_reparse(scope: str, ref: str, db: Session = Depends(database.get_db),
                   user: models.User = Depends(require_capability("catalogue_onboard"))):
    """Re-derive the scoped items from retained text and stage the per-field diff as a batch."""
    resolved = _resolve_items(db, scope, ref)
    if not resolved:
        raise HTTPException(status_code=404, detail="No catalogue items in this scope")
    # one card per SKU — the trusted own-row per product; an over-matched SKU is skipped (ambiguous), never
    # recaptured from another SKU's row
    items, ambiguous = _dedupe_latest(db, resolved)
    now = _now()
    batch = models.ReparseBatch(
        scope_type=scope, scope_ref=str(ref), parser_version=reparse_service.PARSER_VERSION,
        mode="text", status="open", item_count=len(items), changed_count=0,
        created_at=now, created_by=getattr(user, "username", None))
    db.add(batch); db.flush()
    changed = 0
    for item in items:
        for ch in reparse_service.compute_changes(db, item):
            db.add(models.ReparseChange(
                batch_id=batch.id, catalogue_item_id=ch["catalogue_item_id"], product_id=ch["product_id"],
                field=ch["field"], old_value=ch["old_value"], new_value=ch["new_value"],
                affects_cost=1 if ch["affects_cost"] else 0, eff_cost_before=ch["eff_cost_before"],
                eff_cost_after=ch["eff_cost_after"], status="pending"))
            changed += 1
    batch.changed_count = changed
    # newest re-parse of a SKU wins — supersede any prior pending re-parse of these same catalogue items
    # (other suppliers' open re-parses are left alone and stay resumable)
    _supersede_prior(db, batch, [it.id for it in items], getattr(user, "username", None))
    audit_log.record(db, action="catalogue.reparse_start", actor=user, entity_type="reparse_batch",
                     entity_id=batch.id, entity_label=f"{scope}:{ref}",
                     details={"scope": scope, "ref": str(ref), "items": len(items), "changed": changed,
                              "resolved": len(resolved), "deduped_dropped": len(resolved) - len(items),
                              "ambiguous_skipped": ambiguous, "parser_version": reparse_service.PARSER_VERSION})
    db.commit()
    out = _batch_dict(db, batch)
    out["ambiguous_skipped"] = ambiguous    # SKUs skipped because their catalogue rows are an ambiguous mix
    return out
