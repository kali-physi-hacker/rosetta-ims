"""The RECEIPT inbox — mirrors Daysmart receipts (and their rows) onto
the local purchase orders they belong to.

Receipts ride along with the PO pull (Moses's rule: everything attached
to a PO comes with the PO): the full sync reads the receipt list after
the order pages; a single-PO refresh reads that PO's receipts. Same
discipline as the header and line inboxes:

- Match on the Daysmart receipt id (``daysmart_receipt_id``); rows on the
  Daysmart row id (``daysmart_receipt_item_id``), scoped to the receipt.
- Link to OUR purchase order. A POSTED receipt names its order on the
  header (``purchase_order_id``). An UNPOSTED one does not — the list
  says "" and the detail says null (wire, receipt-edit.har) — its ROWS
  name the order. So a new receipt without a header link is DEFERRED by
  ``apply_receipt_page`` and linked by ``link_receipt_from_rows`` once
  its rows are read: every row must name the same, local order. A
  receipt whose order is not local is counted ``unlinked`` and reported,
  never stored blind.
- Ownership split: Rosetta-owned fields (what WE sent: invoice_number,
  receipt_date, amounts, notes; on rows the quantities, lot, expiry…) are
  seeded once for an imported receipt and never touched by a pull; the
  ``daysmart_*`` mirror columns are overwritten every pull. ONE column
  list (``RECEIPT_OWNED_COLUMNS``) drives writer and hash; the detail
  surface applies through ``apply_receipt_header``, which keeps the
  stored spelling of dates and money when the meaning is unchanged, so
  a refresh never churns the hash on unchanged data.
- ``post_status`` follows Daysmart's own receipt status when it is
  known: 'posted' means posted — whoever pressed the button — and a
  receipt Daysmart shows as in process is not posted, whatever we
  believed. Unknown statuses change nothing.
- Rows: a staged ADJUSTMENT (a local item that knows its PO line but not
  yet its generated row) is ADOPTED by the generated row with that exact
  ``purchase_order_item_id`` — never by position. Absence from a page is
  never a delete; ``is_active`` is mirrored. A row from a different
  receipt is refused (issue), never adopted.
- Issues persisted with ``payload_context='fetch_receipts'`` /
  ``'fetch_receipt_rows'``, ``daysmart_id`` = the RECEIPT's Daysmart id,
  deduplicated against open records.

Stages only; the caller owns the commit.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation

import models
from schemas.purchase_orders import (
    DaysmartReceiptPageParseResult,
    DaysmartReceiptRowsParseResult,
    DaysmartReceiptRowV1,
    DaysmartReceiptV1,
)


@dataclass(frozen=True)
class ReceiptsApplySummary:
    created: int
    updated: int
    unchanged: int
    #: Receipts skipped because their purchase order is not local.
    unlinked: int
    issues_recorded: int
    #: New receipts whose header names no order (unposted): their rows
    #: decide — see ``link_receipt_from_rows``.
    deferred: tuple[DaysmartReceiptV1, ...] = ()


@dataclass(frozen=True)
class ReceiptRowsApplySummary:
    created: int
    updated: int
    unchanged: int
    issues_recorded: int
    #: Local adjustments (staged before Daysmart generated its rows) that
    #: were matched to their generated row by PO line and given its id.
    adopted: int = 0


@dataclass(frozen=True)
class ReceiptLinkOutcome:
    #: The local receipt created (and its rows applied), or None.
    local: "models.PoReceipt | None"
    rows: ReceiptRowsApplySummary | None
    issues_recorded: int
    #: Why it stayed unlinked, when it did.
    reason: str | None = None


@dataclass(frozen=True)
class ReceiptRefreshSummary:
    header_changed: bool
    rows: ReceiptRowsApplySummary
    issues_recorded: int


#: THE single declaration of Daysmart-owned receipt columns. The list
#: adapter produces exactly these keys; the detail path applies onto the
#: row and hashes exactly these columns — extend HERE.
RECEIPT_OWNED_COLUMNS: tuple[str, ...] = (
    "daysmart_index", "daysmart_status_id", "daysmart_status_label", "daysmart_status",
    "daysmart_receipt_date", "daysmart_post_at", "daysmart_invoice_number",
    "daysmart_amount", "daysmart_tax", "daysmart_shipping", "daysmart_tax_type",
    "daysmart_notes", "daysmart_is_active", "daysmart_purchase_order_id",
    "daysmart_supplier_id", "daysmart_supplier_name", "daysmart_item_count",
    "daysmart_created_at", "daysmart_updated_at",
)


def apply_receipt_page(db, page: DaysmartReceiptPageParseResult) -> ReceiptsApplySummary:
    """Upsert one page of receipt headers onto their local POs. A new
    receipt without a header order link is deferred (its rows decide)."""
    now = _now_iso()
    created = updated = unchanged = unlinked = 0
    deferred: list[DaysmartReceiptV1] = []

    for receipt in page.receipts:
        values = _daysmart_owned_receipt_values(receipt)
        digest = _content_hash(values)
        existing = (db.query(models.PoReceipt)
                    .filter_by(daysmart_receipt_id=receipt.daysmart_id).first())
        if existing is None:
            if receipt.purchase_order_id is None:
                deferred.append(receipt)
                continue
            po = (db.query(models.PurchaseOrder)
                  .filter_by(daysmart_id=receipt.purchase_order_id).first())
            if po is None:
                unlinked += 1
                continue
            _create_local_receipt(db, receipt, po, values, digest, now)
            created += 1
        elif existing.content_hash == digest:
            existing.last_seen_at = now
            unchanged += 1
        else:
            _apply_values(existing, values, digest, now)
            existing.post_status = _post_status_for(receipt.status, existing.post_status)
            existing.updated_at = now
            updated += 1

    issues_recorded = 0
    for issue in page.issues:
        if _record_issue(db, code=issue.code.value, field_path=issue.field_path,
                         raw_value=issue.raw_value, expected=issue.expected,
                         daysmart_id=issue.daysmart_id, purchase_order_id=None,
                         context="fetch_receipts", now=now):
            issues_recorded += 1
    return ReceiptsApplySummary(created=created, updated=updated, unchanged=unchanged,
                                unlinked=unlinked, issues_recorded=issues_recorded,
                                deferred=tuple(deferred))


def link_receipt_from_rows(db, receipt: DaysmartReceiptV1,
                           rows: DaysmartReceiptRowsParseResult) -> ReceiptLinkOutcome:
    """A deferred (unposted) receipt: its rows must all name ONE order,
    and that order must be local. Then it is created and its rows applied
    in the same breath."""
    now = _now_iso()
    orders = {row.purchase_order_id for row in rows.rows if row.purchase_order_id is not None}
    if not rows.rows or not orders:
        return ReceiptLinkOutcome(local=None, rows=None, issues_recorded=0,
                                  reason="its rows name no purchase order")
    if len(orders) > 1:
        recorded = 1 if _record_issue(
            db, code="PO_FIELD_INVALID", field_path="receipt_items[].purchase_order_id",
            raw_value=sorted(orders), expected="one purchase order for every row",
            daysmart_id=receipt.daysmart_id, purchase_order_id=None,
            context="fetch_receipt_rows", now=now) else 0
        return ReceiptLinkOutcome(local=None, rows=None, issues_recorded=recorded,
                                  reason="its rows name more than one purchase order")
    (order_id,) = orders
    po = db.query(models.PurchaseOrder).filter_by(daysmart_id=order_id).first()
    if po is None:
        return ReceiptLinkOutcome(local=None, rows=None, issues_recorded=0,
                                  reason=f"its order {order_id} is not local")
    values = _daysmart_owned_receipt_values(receipt)
    local = _create_local_receipt(db, receipt, po, values, _content_hash(values), now)
    applied = apply_receipt_rows(db, local, rows)
    return ReceiptLinkOutcome(local=local, rows=applied, issues_recorded=applied.issues_recorded)


def apply_receipt_header(db, local, receipt: DaysmartReceiptV1, now: str | None = None) -> bool:
    """The DETAIL surface (or a refreshed header) onto an existing local
    receipt. Values with more than one wire spelling keep the STORED
    spelling when their meaning is unchanged; ``daysmart_item_count`` is
    only on the list, so it is kept. Returns whether the hash moved."""
    now = now or _now_iso()
    incoming = _daysmart_owned_receipt_values(receipt)
    for column in ("daysmart_receipt_date", "daysmart_post_at",
                   "daysmart_created_at", "daysmart_updated_at"):
        if _same_instant(getattr(local, column), incoming[column]):
            incoming.pop(column)
    for column in ("daysmart_amount", "daysmart_tax", "daysmart_shipping"):
        if _same_money(getattr(local, column), incoming[column]):
            incoming.pop(column)
    if incoming["daysmart_item_count"] is None:
        incoming.pop("daysmart_item_count")
    before = local.content_hash
    for column, value in incoming.items():
        setattr(local, column, value)
    local.content_hash = _content_hash(_owned_values_from_row(local))
    local.last_seen_at = now
    if local.content_hash == before:
        return False
    local.post_status = _post_status_for(receipt.status, local.post_status)
    local.updated_at = now
    return True


def refresh_receipt(db, local, daysmart) -> ReceiptRefreshSummary:
    """Pull ONE receipt — header and rows from the detail surface. Raises
    the adapter's typed errors; the caller reports them."""
    if local.daysmart_receipt_id is None:
        raise ValueError(f"receipt {local.receipt_uuid} is not synced — nothing to pull")
    detail = daysmart.fetch_receipt(local.daysmart_receipt_id)
    now = _now_iso()
    header_changed = False
    if detail.receipt is not None:
        header_changed = apply_receipt_header(db, local, detail.receipt, now)
    # Header issues carry the receipt's id; row issues carry a row id (or
    # none) — file each under its own context.
    header_issues = [i for i in detail.issues if i.daysmart_id == detail.receipt_daysmart_id]
    row_issues = [i for i in detail.issues if i.daysmart_id != detail.receipt_daysmart_id]
    issues = 0
    for issue in header_issues:
        if _record_issue(db, code=issue.code.value, field_path=issue.field_path,
                         raw_value=issue.raw_value, expected=issue.expected,
                         daysmart_id=local.daysmart_receipt_id,
                         purchase_order_id=local.purchase_order_id,
                         context="fetch_receipts", now=now):
            issues += 1
    rows = apply_receipt_rows(db, local, DaysmartReceiptRowsParseResult(
        rows=detail.rows, issues=row_issues, receipt_daysmart_id=detail.receipt_daysmart_id))
    return ReceiptRefreshSummary(header_changed=header_changed, rows=rows,
                                 issues_recorded=issues + rows.issues_recorded)


def refresh_receipt_rows(db, receipt, daysmart) -> ReceiptRowsApplySummary:
    """Fetch this receipt's generated rows from Daysmart and mirror them.
    Raises the adapter's typed errors (the caller reports them)."""
    if receipt.daysmart_receipt_id is None:
        raise ValueError(f"receipt {receipt.receipt_uuid} is not synced — no Daysmart receipt to read rows from")
    result = daysmart.fetch_receipt_rows(receipt.daysmart_receipt_id)
    return apply_receipt_rows(db, receipt, result)


def apply_receipt_rows(db, receipt, result: DaysmartReceiptRowsParseResult) -> ReceiptRowsApplySummary:
    if receipt.daysmart_receipt_id is None:
        raise ValueError(f"receipt {receipt.receipt_uuid} is not synced — cannot apply rows")
    now = _now_iso()
    po = receipt.purchase_order
    lines_by_daysmart_id = {line.daysmart_item_id: line
                            for line in po.lines if line.daysmart_item_id is not None}
    by_row_id = {item.daysmart_receipt_item_id: item
                 for item in receipt.items if item.daysmart_receipt_item_id is not None}
    # Adjustments staged before the create know their PO line but not yet
    # their generated row: adopt them by that exact line identity (one
    # generated row per PO line — never by position, never by guess).
    pending_by_line: dict[int, list] = {}
    for item in receipt.items:
        if item.daysmart_receipt_item_id is None and item.daysmart_po_item_id is not None:
            pending_by_line.setdefault(item.daysmart_po_item_id, []).append(item)
    created = updated = unchanged = adopted = issues_recorded = 0

    for row in result.rows:
        if row.receipt_id is not None and row.receipt_id != receipt.daysmart_receipt_id:
            if _record_issue(db, code="PO_FIELD_INVALID",
                             field_path=f"row[{row.daysmart_row_id}].purchase_order_receipt_id",
                             raw_value=row.receipt_id,
                             expected=f"this receipt's id {receipt.daysmart_receipt_id}",
                             daysmart_id=receipt.daysmart_receipt_id,
                             purchase_order_id=receipt.purchase_order_id,
                             context="fetch_receipt_rows", now=now):
                issues_recorded += 1
            continue
        values = _daysmart_owned_row_values(row)
        digest = _content_hash(values)
        line = lines_by_daysmart_id.get(row.po_line_id) if row.po_line_id is not None else None
        item = by_row_id.get(row.daysmart_row_id)
        if item is None and row.po_line_id is not None and row.po_line_id in pending_by_line:
            candidates = pending_by_line.pop(row.po_line_id)
            if len(candidates) != 1:
                if _record_issue(db, code="PO_FIELD_INVALID",
                                 field_path=f"row[{row.daysmart_row_id}].purchase_order_item_id",
                                 raw_value=row.po_line_id,
                                 expected=f"one local adjustment for this PO line, found {len(candidates)}",
                                 daysmart_id=receipt.daysmart_receipt_id,
                                 purchase_order_id=receipt.purchase_order_id,
                                 context="fetch_receipt_rows", now=now):
                    issues_recorded += 1
                continue
            item = candidates[0]
            item.daysmart_receipt_item_id = row.daysmart_row_id
            if item.purchase_order_line_id is None and line is not None:
                item.purchase_order_line_id = line.id
            if item.post_to_item_id is None:
                item.post_to_item_id = row.post_to_item_id
            _apply_values(item, values, digest, now)      # the mirror; OUR fields are the adjustment
            by_row_id[row.daysmart_row_id] = item
            adopted += 1
            continue
        if item is None:
            item = models.PoReceiptItem(
                daysmart_receipt_item_id=row.daysmart_row_id,
                daysmart_po_item_id=row.po_line_id,
                purchase_order_line_id=line.id if line is not None else None,
                post_to_item_id=row.post_to_item_id,
                # Rosetta-owned, SEEDED once from Daysmart for an imported row:
                quantity_received=_text(row.quantity_received),
                quantity_in_stock=_text(row.quantity_in_stock),
                amount=row.amount_raw or "",
                expiration_date=_text(row.expiration_date) or None,
                lot_number=row.lot_number or "",
                manufacturer=row.manufacturer,
                ndc_code=row.ndc_code,
                tax=row.tax_raw,
                sync_status="synced",
            )
            _apply_values(item, values, digest, now)
            receipt.items.append(item)
            by_row_id[row.daysmart_row_id] = item
            created += 1
        else:
            # Identity links a write flow could not learn yet — filled, never changed.
            if item.daysmart_po_item_id is None:
                item.daysmart_po_item_id = row.po_line_id
            if item.purchase_order_line_id is None and line is not None:
                item.purchase_order_line_id = line.id
            if item.post_to_item_id is None:
                item.post_to_item_id = row.post_to_item_id
            if item.content_hash == digest:
                item.last_seen_at = now
                unchanged += 1
            else:
                _apply_values(item, values, digest, now)
                updated += 1

    for issue in result.issues:
        prefix = f"row[{issue.daysmart_id}]." if issue.daysmart_id not in (None, result.receipt_daysmart_id) else "row[?]."
        if _record_issue(db, code=issue.code.value, field_path=prefix + issue.field_path,
                         raw_value=issue.raw_value, expected=issue.expected,
                         daysmart_id=receipt.daysmart_receipt_id,
                         purchase_order_id=receipt.purchase_order_id,
                         context="fetch_receipt_rows", now=now):
            issues_recorded += 1

    db.flush()
    return ReceiptRowsApplySummary(created=created, updated=updated, unchanged=unchanged,
                                   issues_recorded=issues_recorded, adopted=adopted)


# ---------------------------------------------------------------------------


def _create_local_receipt(db, receipt: DaysmartReceiptV1, po, values: dict, digest: str, now: str):
    row = models.PoReceipt(
        purchase_order_id=po.id,
        daysmart_receipt_id=receipt.daysmart_id,
        sync_status="synced",            # it exists in Daysmart — that IS synced
        post_status=_post_status_for(receipt.status, "draft"),
        # Rosetta-owned, SEEDED once from Daysmart for an imported receipt:
        invoice_number=receipt.invoice_number,
        receipt_date=receipt.receipt_date.isoformat(),
        amount=_money_str(receipt.amount),
        shipping=_money_str(receipt.shipping),
        tax=_money_str(receipt.tax),
        tax_type=receipt.tax_type,
        notes=receipt.notes,
        created_at=now,
        updated_at=now,
    )
    _apply_values(row, values, digest, now)
    db.add(row)
    db.flush()
    return row


def _daysmart_owned_receipt_values(receipt: DaysmartReceiptV1) -> dict:
    """The list/detail contract → ``RECEIPT_OWNED_COLUMNS`` (drives both
    the writer and the content hash)."""
    return {
        "daysmart_index": receipt.index,
        "daysmart_status_id": receipt.status_id,
        "daysmart_status_label": receipt.status_label,
        "daysmart_status": receipt.status,
        "daysmart_receipt_date": receipt.receipt_date.isoformat(),
        "daysmart_post_at": receipt.post_at.isoformat() if receipt.post_at else None,
        "daysmart_invoice_number": receipt.invoice_number,
        "daysmart_amount": _money_str(receipt.amount),
        "daysmart_tax": _money_str(receipt.tax),
        "daysmart_shipping": _money_str(receipt.shipping),
        "daysmart_tax_type": receipt.tax_type,
        "daysmart_notes": receipt.notes,
        "daysmart_is_active": receipt.is_active,
        "daysmart_purchase_order_id": receipt.purchase_order_id,
        "daysmart_supplier_id": receipt.supplier_daysmart_id,
        "daysmart_supplier_name": receipt.supplier.name if receipt.supplier else None,
        "daysmart_item_count": receipt.item_count,
        "daysmart_created_at": receipt.created_at.isoformat(),
        "daysmart_updated_at": receipt.updated_at.isoformat(),
    }


def _owned_values_from_row(local) -> dict:
    return {column: getattr(local, column) for column in RECEIPT_OWNED_COLUMNS}


def _daysmart_owned_row_values(row: DaysmartReceiptRowV1) -> dict:
    return {
        "daysmart_quantity_received": row.quantity_received,
        "daysmart_quantity_in_stock": row.quantity_in_stock,
        "daysmart_quantity_rejected": row.quantity_rejected,
        "daysmart_amount_raw": row.amount_raw,
        "daysmart_tax_raw": row.tax_raw,
        "daysmart_lot_number": row.lot_number,
        "daysmart_expiration_date": row.expiration_date,
        "daysmart_manufacturer": row.manufacturer,
        "daysmart_ndc_code": row.ndc_code,
        "daysmart_status_id": row.status_id,
        "daysmart_is_active": row.is_active,
        "daysmart_post_to_item_id": row.post_to_item_id,
        "daysmart_updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


def _post_status_for(status: str | None, current: str) -> str:
    """Daysmart's known status wins; an unknown one changes nothing."""
    if status == "posted":
        return "posted"
    if status == "in_process":
        return "draft"
    return current


def _apply_values(target, values: dict, digest: str, now: str) -> None:
    for column, value in values.items():
        setattr(target, column, value)
    target.content_hash = digest
    target.last_seen_at = now


def _content_hash(values: dict) -> str:
    material = json.dumps(values, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def _same_instant(stored: str | None, incoming: str | None) -> bool:
    if stored is None or incoming is None:
        return stored is None and incoming is None
    try:
        a, b = datetime.fromisoformat(stored), datetime.fromisoformat(incoming)
    except ValueError:
        return False
    return a.tzinfo is not None and b.tzinfo is not None and a == b


def _same_money(stored: str | None, incoming: str | None) -> bool:
    if stored is None or incoming is None:
        return stored is None and incoming is None
    try:
        return Decimal(stored) == Decimal(incoming)
    except InvalidOperation:
        return False


def _record_issue(db, *, code: str, field_path: str, raw_value, expected: str,
                  daysmart_id, purchase_order_id, context: str, now: str) -> bool:
    exists = (db.query(models.PoValidationIssueRecord)
              .filter_by(code=code, field_path=field_path, daysmart_id=daysmart_id,
                         payload_context=context, resolution_status="open")
              .first() is not None)
    if exists:
        return False
    db.add(models.PoValidationIssueRecord(
        code=code, field_path=field_path,
        raw_value=None if raw_value is None else str(raw_value),
        expected=expected, daysmart_id=daysmart_id,
        purchase_order_id=purchase_order_id, payload_context=context, created_at=now))
    return True


def _money_str(value) -> str | None:
    return None if value is None else str(value)


def _text(value) -> str:
    return "" if value is None else str(value)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
