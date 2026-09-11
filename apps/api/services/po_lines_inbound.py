"""The order-LINES inbox — mirrors a PO's lines from Daysmart (phase 3a+).

The header inbox imported headers only; every imported PO was a header
with an empty line list. This closes that gap with the same discipline:

- Match on the Daysmart LINE id (``daysmart_item_id``), scoped to the PO.
- Ownership split: Rosetta-owned line fields (description, quantity, uom,
  unit_price_raw, notes — what WE ordered) are seeded once for an imported
  line and never touched by fetches; the ``daysmart_*`` mirror columns are
  overwritten every fetch. ONE field map drives writer and hash.
- Absence from a page is never a delete; ``is_active`` is mirrored, not
  inferred.
- A line from a different order is refused (issue), never adopted.
- Issues persisted with ``payload_context='fetch_lines'``, deduplicated
  against open records.

``refresh_order_lines`` = fetch + apply for one PO — the worker behind
every pull (the full sync mirrors lines for every order on every page;
the single-PO refresh does header + lines). It raises the adapter's typed
errors and the caller REPORTS them (which order, which error). Nothing is
marked for a later retry — a failed pull is re-run by a person, on purpose.

Stages only; the caller owns the commit.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone

import models
from schemas.purchase_orders import DaysmartOrderItemPageParseResult, DaysmartOrderItemV1
from services.daysmart_service import DaysmartError  # noqa: F401  (re-exported for callers)


@dataclass(frozen=True)
class LinesApplySummary:
    created: int
    updated: int
    unchanged: int
    issues_recorded: int


def refresh_order_lines(db, po, daysmart) -> LinesApplySummary:
    """Fetch this PO's lines from Daysmart and mirror them. Raises the
    adapter's typed errors on transport failure (the caller decides)."""
    if po.daysmart_id is None:
        raise ValueError(f"PO {po.po_uuid} is not synced — no Daysmart order to read lines from")
    page = daysmart.fetch_order_lines(po.daysmart_id)
    return apply_order_lines(db, po, page)


def apply_order_lines(db, po, page: DaysmartOrderItemPageParseResult) -> LinesApplySummary:
    if po.daysmart_id is None:
        raise ValueError(f"PO {po.po_uuid} is not synced — cannot apply lines")
    now = _now_iso()
    by_line_id = {line.daysmart_item_id: line
                  for line in po.lines if line.daysmart_item_id is not None}
    created = updated = unchanged = issues_recorded = 0

    for item in page.items:
        if item.purchase_order_id is not None and item.purchase_order_id != po.daysmart_id:
            if _record_issue(db, po, code="PO_FIELD_INVALID",
                             field_path=f"line[{item.daysmart_line_id}].purchase_order_id",
                             raw_value=item.purchase_order_id,
                             expected=f"this order's id {po.daysmart_id}", now=now):
                issues_recorded += 1
            continue
        values = _daysmart_owned_line_values(item)
        digest = _content_hash(values)
        line = by_line_id.get(item.daysmart_line_id)
        if line is None:
            line = models.PurchaseOrderLine(
                # Rosetta-owned, SEEDED once from Daysmart for an imported line:
                description=item.item_name or f"Daysmart item {item.inventory_item_id}",
                quantity=str(item.quantity_ordered),
                quantity_uom=item.container_type or "UNIT",
                unit_price_raw=item.price_raw,
                notes=item.notes,
                sync_status="synced",          # it exists in Daysmart — that IS synced
                daysmart_item_id=item.daysmart_line_id,
                daysmart_inventory_item_id=item.inventory_item_id,
            )
            _apply_values(line, values, digest, now)
            po.lines.append(line)
            by_line_id[item.daysmart_line_id] = line
            created += 1
        elif line.content_hash == digest:
            line.last_seen_at = now
            unchanged += 1
        else:
            _apply_values(line, values, digest, now)
            updated += 1

    for issue in page.issues:
        prefix = f"line[{issue.daysmart_id}]." if issue.daysmart_id is not None else "line[?]."
        if _record_issue(db, po, code=issue.code.value,
                         field_path=prefix + issue.field_path,
                         raw_value=issue.raw_value, expected=issue.expected, now=now):
            issues_recorded += 1

    db.flush()
    return LinesApplySummary(created=created, updated=updated,
                             unchanged=unchanged, issues_recorded=issues_recorded)


# ---------------------------------------------------------------------------


def _daysmart_owned_line_values(item: DaysmartOrderItemV1) -> dict:
    """THE single declaration of Daysmart-owned line columns — drives both
    the writer and the content hash. Extend here and both stay in step."""
    return {
        "daysmart_inventory_item_id": item.inventory_item_id,
        "daysmart_item_name": item.item_name,
        "daysmart_quantity_ordered": item.quantity_ordered,
        "daysmart_quantity_received": item.quantity_received,
        "daysmart_back_ordered": item.back_ordered,
        "daysmart_price_raw": item.price_raw,
        "daysmart_line_total_raw": item.line_total_raw,
        "daysmart_container_type": item.container_type,
        "daysmart_catalogue_cost_raw": item.catalogue_cost_raw,
        "daysmart_is_active": item.is_active,
        "daysmart_updated_at": item.updated_at.isoformat() if item.updated_at else None,
    }


def _apply_values(line, values: dict, digest: str, now: str) -> None:
    for column, value in values.items():
        setattr(line, column, value)
    line.content_hash = digest
    line.last_seen_at = now


def _content_hash(values: dict) -> str:
    material = json.dumps(values, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def _record_issue(db, po, *, code: str, field_path: str, raw_value, expected: str, now: str) -> bool:
    """Persist a line issue unless an identical OPEN one exists. Returns
    True when a row was added."""
    exists = (db.query(models.PoValidationIssueRecord)
              .filter_by(code=code, field_path=field_path, daysmart_id=po.daysmart_id,
                         payload_context="fetch_lines", resolution_status="open")
              .first() is not None)
    if exists:
        return False
    db.add(models.PoValidationIssueRecord(
        code=code, field_path=field_path,
        raw_value=None if raw_value is None else str(raw_value),
        expected=expected, daysmart_id=po.daysmart_id,
        purchase_order_id=po.id, payload_context="fetch_lines", created_at=now))
    return True


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
