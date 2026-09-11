"""Inbound PULL (Daysmart → Rosetta): apply fetched orders — and their
lines — to our database (phase 3a).

The "inbox" half of CIS-09, as an idempotent upsert:

- Match on ``daysmart_id`` only — never index, never content.
- Field ownership IS the reconciliation policy: ONE column list
  (``OWNED_COLUMNS``) declares the Daysmart-owned header columns; both
  dialect adapters produce exactly those keys and the change-detection
  hash is always computed over exactly those keys — writer and hash
  cannot drift apart (they once did: review finding H8). The derived
  ``daysmart_status`` participates, so growing the status vocabulary
  backfills previously-unknown statuses on the next fetch.
- Contract issues from a page are persisted for the OPS review screen —
  deduplicated against existing OPEN records, so a persistent issue is one
  row, not one row per poll (H5). The offending order is skipped whole.
- Absence is not deletion: orders missing from a page are left alone.
- ``apply_order_page`` NEVER advances the watermark (H1): Daysmart pages
  newest-first, so a per-page advance skips the rest of the cycle forever
  if the cycle dies. ``run_full_inbound_sync`` drives all pages and
  advances ONCE, only after the whole header cycle completed.
- LINES ride along with every pull, unconditionally: after each header
  page is applied, every order on it has its lines mirrored
  (``po_lines_inbound``). There is NO trigger — an edited line changes
  nothing on the header (not even ``item_count``), so nothing on the
  header can be trusted to say "lines changed" — and NO deferred retry:
  a line pull that fails is REPORTED in the summary (which order, which
  error) for a person to re-run, never queued for a later cycle.

- RECEIPTS ride along too (``po_receipts_inbound``): after the order
  pages, the full sync reads the receipt list and, for every receipt that
  belongs to a local order, its generated rows; a single-PO refresh reads
  that order's receipts. Receipt failures are reported the same way —
  per receipt, never queued — and never touch the header watermark.

Two entry points:

- ``run_full_inbound_sync``: every page, every order — headers + lines,
  then receipts + rows. The initial import is exactly this with an empty
  watermark; there is no separate importer.
- ``refresh_purchase_order``: ONE order — header from the detail surface
  (``order/{id}``, the create dialect) + lines + receipts + rows. ALL
  reads happen before anything is applied, so a failure leaves the rows
  untouched.

Everything here stages only; the caller owns the commit.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation

import models
from schemas.purchase_orders import (
    DaysmartCreatedOrderV1,
    DaysmartOrderPageParseResult,
    DaysmartOrderV1,
    DaysmartReceiptRowsParseResult,
)
from services.daysmart_service import DaysmartError, DaysmartMalformedResponse
from services.po_lines_inbound import (
    LinesApplySummary,
    apply_order_lines,
    refresh_order_lines,
)
from services.po_receipts_inbound import (
    ReceiptsApplySummary,
    apply_receipt_header,
    apply_receipt_page,
    apply_receipt_rows,
    link_receipt_from_rows,
    refresh_receipt_rows,
)
from services.po_sync_state import advance_watermark


@dataclass(frozen=True)
class ApplySummary:
    created: int
    updated: int
    unchanged: int
    issues_recorded: int


@dataclass(frozen=True)
class LineFailure:
    """One order whose lines could not be pulled this cycle — reported,
    never queued."""
    daysmart_id: int
    error_code: str      # the adapter's error class name
    message: str


@dataclass(frozen=True)
class ReceiptFailure:
    """One receipt whose rows could not be pulled — reported, never queued."""
    daysmart_receipt_id: int
    error_code: str
    message: str


@dataclass(frozen=True)
class ReceiptPullSummary:
    #: Every receipt-list page was read (a page failure stops the receipt
    #: pull, is reported in ``error``, and never touches the header watermark).
    completed: bool
    pages_applied: int
    created: int
    updated: int
    unchanged: int
    unlinked: int
    issues_recorded: int
    rows_synced: int
    rows_failed: int
    failures: tuple[ReceiptFailure, ...] = ()
    error: str | None = None


@dataclass(frozen=True)
class FullSyncSummary:
    completed: bool
    pages_applied: int
    created: int
    updated: int
    unchanged: int
    issues_recorded: int
    #: The watermark after the cycle (unchanged when not completed).
    watermark: str | None
    #: Orders whose lines were mirrored this cycle, how many were not, and
    #: exactly which ones (with the error) — for the caller to show.
    lines_synced: int = 0
    lines_failed: int = 0
    line_failures: tuple[LineFailure, ...] = ()
    #: The receipt pull that followed the order pages (skipped, and said
    #: so, when the order cycle did not complete).
    receipts: ReceiptPullSummary | None = None


@dataclass(frozen=True)
class RefreshSummary:
    header_changed: bool
    lines: LinesApplySummary
    issues_recorded: int
    receipts: ReceiptPullSummary | None = None


class DaysmartOrderUnreadable(Exception):
    """The detail surface answered, but not with a readable order — the
    named contract issues say why."""

    def __init__(self, daysmart_id: int, issues) -> None:
        super().__init__(
            f"Daysmart order {daysmart_id} is unreadable: "
            + ", ".join(f"{i.field_path} ({i.code.value})" for i in issues))
        self.daysmart_id = daysmart_id
        self.issues = list(issues)


#: THE single declaration of Daysmart-owned header columns. Both dialect
#: adapters below produce exactly these keys, and the content hash is
#: always computed over exactly these keys — extend HERE and everything
#: stays in step.
OWNED_COLUMNS: tuple[str, ...] = (
    "daysmart_index", "supplier_daysmart_id", "supplier_name", "account_no",
    "order_date", "ship_to_id", "bill_to_id", "payment_terms", "notes",
    "order_name", "sub_total", "shipping", "tax", "total",
    "daysmart_status_id", "daysmart_status_label", "daysmart_status",
    "is_active", "daysmart_business_id", "daysmart_created_by",
    "daysmart_created_at", "daysmart_updated_by", "daysmart_updated_at",
    # Daysmart's line count as the list reports it — a display value that
    # decides nothing (lines are pulled every time regardless).
    "item_count",
)


def apply_order_page(db, page: DaysmartOrderPageParseResult) -> ApplySummary:
    """Upsert one fetched page of HEADERS. Idempotent: re-applying
    converges to all-unchanged. Does NOT touch the watermark — that is
    cycle-level bookkeeping owned by ``run_full_inbound_sync``."""
    now = _now_iso()
    created = updated = unchanged = 0

    for order in page.orders:
        values = _daysmart_owned_values(order)
        digest = _content_hash(values)
        existing = (db.query(models.PurchaseOrder)
                    .filter_by(daysmart_id=order.daysmart_id).first())
        if existing is None:
            po = models.PurchaseOrder(
                sync_status="synced",   # it exists in Daysmart — that IS synced
                daysmart_id=order.daysmart_id,
                created_at=now,
                updated_at=now,
            )
            _apply_values(po, values, digest, now)
            db.add(po)
            # Flush so the SAME order appearing again this cycle (it shifted
            # pages under a concurrent create) is found by the query above
            # instead of double-inserting under autoflush=False (H7).
            db.flush()
            created += 1
        elif existing.content_hash == digest:
            existing.last_seen_at = now
            unchanged += 1
        else:
            _apply_values(existing, values, digest, now)
            updated += 1

    issues_recorded = _record_header_issues(db, page.issues, now)
    return ApplySummary(created=created, updated=updated, unchanged=unchanged,
                        issues_recorded=issues_recorded)


def run_full_inbound_sync(db, daysmart, *, max_pages: int = 50) -> FullSyncSummary:
    """One complete pull: fetch every page, apply its headers, mirror the
    lines of every order on it, and advance the watermark ONCE — only if
    the entire header cycle succeeded.

    A header failure mid-cycle keeps every page already applied (their
    data is correct, their lines were pulled) but leaves the bookmark
    alone, so the next cycle re-reads instead of silently skipping the
    pages this one never saw. Quarantined orders (parse issues) never
    advance past either — their issues stay open on the review screen,
    their data is re-fetched next cycle, and their lines are not pulled
    (no header, no lines).
    """
    created = updated = unchanged = issues = 0
    lines_synced = lines_failed = 0
    failures: list[LineFailure] = []
    newest_seen: datetime | None = None
    page_number = 0
    last_page = 1

    while page_number < last_page and page_number < max_pages:
        page_number += 1
        try:
            page = daysmart.fetch_orders(page=page_number)
        except DaysmartError:
            return FullSyncSummary(
                completed=False, pages_applied=page_number - 1,
                created=created, updated=updated, unchanged=unchanged,
                issues_recorded=issues, watermark=_current_watermark(db),
                lines_synced=lines_synced, lines_failed=lines_failed,
                line_failures=tuple(failures),
                receipts=ReceiptPullSummary(
                    completed=False, pages_applied=0, created=0, updated=0, unchanged=0,
                    unlinked=0, issues_recorded=0, rows_synced=0, rows_failed=0,
                    error="skipped: the order pull did not complete"))
        last_page = max(last_page, page.meta.last_page)

        summary = apply_order_page(db, page)
        created += summary.created
        updated += summary.updated
        unchanged += summary.unchanged
        issues += summary.issues_recorded
        for order in page.orders:
            if newest_seen is None or order.updated_at > newest_seen:
                newest_seen = order.updated_at

        # Lines ride along: every order on this page, every time.
        ok, failed = _mirror_lines(db, daysmart, [o.daysmart_id for o in page.orders], failures)
        lines_synced += ok
        lines_failed += failed

    watermark = _current_watermark(db)
    if newest_seen is not None:
        watermark = advance_watermark(db, newest_seen.isoformat())

    # Receipts ride along: every receipt of every local order, every time.
    receipts = _pull_receipts(db, daysmart, max_pages=max_pages)
    return FullSyncSummary(
        completed=True, pages_applied=page_number,
        created=created, updated=updated, unchanged=unchanged,
        issues_recorded=issues, watermark=watermark,
        lines_synced=lines_synced, lines_failed=lines_failed,
        line_failures=tuple(failures), receipts=receipts)


def refresh_purchase_order(db, po, daysmart) -> RefreshSummary:
    """Pull ONE order now: its header from the detail surface and its
    lines. Both reads complete before anything is applied — a failure
    (typed adapter error, or ``DaysmartOrderUnreadable``) leaves the row
    exactly as it was, for the caller to report."""
    if po.daysmart_id is None:
        raise ValueError(f"PO {po.po_uuid} is not synced — no Daysmart order to pull")
    result = daysmart.get_order(po.daysmart_id)
    if result.order is None:
        raise DaysmartOrderUnreadable(po.daysmart_id, result.issues)
    if result.order.daysmart_id != po.daysmart_id:
        raise DaysmartMalformedResponse(
            f"order/{po.daysmart_id} answered with order {result.order.daysmart_id}")
    page = daysmart.fetch_order_lines(po.daysmart_id)
    # This order's receipts — read before anything is applied. The filtered
    # list finds receipts whose HEADER names this order (posted ones; an
    # unposted receipt names its order only on its rows, so the filter may
    # miss it). Every listed receipt's rows are read too — they decide the
    # link for unposted ones. Then every LOCAL receipt of this PO the list
    # did not return (our own, still unposted) is read from the detail.
    receipt_pages = _read_receipt_pages(daysmart, purchase_order_id=po.daysmart_id)
    listed = [r for rp in receipt_pages for r in rp.receipts
              if r.purchase_order_id in (None, po.daysmart_id)]
    rows_by_receipt = {r.daysmart_id: daysmart.fetch_receipt_rows(r.daysmart_id) for r in listed}
    local_receipts = [r for r in po.receipts
                      if r.daysmart_receipt_id is not None and r.daysmart_receipt_id not in rows_by_receipt]
    details = {r.daysmart_receipt_id: daysmart.fetch_receipt(r.daysmart_receipt_id) for r in local_receipts}

    now = _now_iso()
    header_changed = _apply_detail_header(po, result.order, page.meta.total, now)
    issues_recorded = _record_header_issues(db, result.issues, now)
    lines = apply_order_lines(db, po, page)

    created = updated = unchanged = unlinked = receipt_issues = rows_synced = 0
    for receipt_page in receipt_pages:
        summary = apply_receipt_page(db, receipt_page)
        created += summary.created
        updated += summary.updated
        unchanged += summary.unchanged
        unlinked += summary.unlinked
        receipt_issues += summary.issues_recorded
        for deferred in summary.deferred:
            rows = rows_by_receipt.get(deferred.daysmart_id)
            if rows is None:
                continue
            if not all(row.purchase_order_id == po.daysmart_id for row in rows.rows):
                unlinked += 1                  # someone else's receipt on the filter
                continue
            outcome = link_receipt_from_rows(db, deferred, rows)
            receipt_issues += outcome.issues_recorded
            if outcome.local is None:
                unlinked += 1
            else:
                created += 1
                rows_synced += 1
                rows_by_receipt.pop(deferred.daysmart_id, None)
    for rid, rows in rows_by_receipt.items():
        local = db.query(models.PoReceipt).filter_by(daysmart_receipt_id=rid).first()
        if local is None:
            continue
        receipt_issues += apply_receipt_rows(db, local, rows).issues_recorded
        rows_synced += 1
    for local in local_receipts:
        detail = details[local.daysmart_receipt_id]
        if detail.receipt is not None and apply_receipt_header(db, local, detail.receipt, now):
            updated += 1
        else:
            unchanged += 1
        receipt_issues += apply_receipt_rows(db, local, DaysmartReceiptRowsParseResult(
            rows=detail.rows, issues=detail.issues,
            receipt_daysmart_id=detail.receipt_daysmart_id)).issues_recorded
        rows_synced += 1
    receipts = ReceiptPullSummary(
        completed=True, pages_applied=len(receipt_pages), created=created, updated=updated,
        unchanged=unchanged, unlinked=unlinked, issues_recorded=receipt_issues,
        rows_synced=rows_synced, rows_failed=0)
    return RefreshSummary(header_changed=header_changed, lines=lines,
                          issues_recorded=issues_recorded + lines.issues_recorded,
                          receipts=receipts)


# ---------------------------------------------------------------------------


def _daysmart_owned_values(order: DaysmartOrderV1) -> dict:
    """The LIST dialect → ``OWNED_COLUMNS`` (keys are column names, values
    JSON-able). Fields Daysmart sends but we don't persist — e.g. name —
    are deliberately absent: they must not burn update writes."""
    return {
        "daysmart_index": order.index,
        "supplier_daysmart_id": order.supplier.daysmart_id,
        "supplier_name": order.supplier.name,
        "account_no": order.account_no,
        "order_date": order.order_date.isoformat(),
        "ship_to_id": order.ship_to_id,
        "bill_to_id": order.bill_to_id,
        "payment_terms": order.payment_terms,
        "notes": order.notes,
        "order_name": order.order_name,
        "sub_total": _money_str(order.sub_total),
        "shipping": _money_str(order.shipping),
        "tax": _money_str(order.tax),
        "total": _money_str(order.total),
        "daysmart_status_id": order.status_id,
        "daysmart_status_label": order.status_label,
        "daysmart_status": order.status,
        "is_active": order.is_active,
        "daysmart_business_id": order.business_id,
        "daysmart_created_by": order.created_by,
        "daysmart_created_at": order.created_at.isoformat(),
        "daysmart_updated_by": order.updated_by,
        "daysmart_updated_at": order.updated_at.isoformat(),
        "item_count": order.item_count,
    }


def apply_order_header_from_detail(po: "models.PurchaseOrder", order: DaysmartCreatedOrderV1,
                                   now: str | None = None) -> bool:
    """Mirror a header the detail/create dialect just answered with (the
    echo of an update or a delete, or a detail read) — the line count is
    not on that surface and is kept. Returns whether the hash moved."""
    return _apply_detail_header(po, order, None, now or _now_iso())


def _apply_detail_header(po: "models.PurchaseOrder", order: DaysmartCreatedOrderV1,
                         line_total: int | None, now: str) -> bool:
    """The DETAIL dialect → the same ``OWNED_COLUMNS``, applied onto the
    row; returns whether the content hash moved.

    The two surfaces spell the same facts differently (list: ISO +08:00
    dates and "" empties; detail: PHP-UTC dates and nulls), so values with
    more than one wire spelling keep the STORED spelling when their
    meaning is unchanged — a refresh must not churn the hash, or burn an
    update on the next list pull, over unchanged data. The hash is then
    recomputed over the row's ``OWNED_COLUMNS``, exactly as the list path
    computes it."""
    incoming: dict = {
        "daysmart_index": order.index,
        "supplier_daysmart_id": order.supplier_daysmart_id,
        "account_no": order.account_no,
        "ship_to_id": order.ship_to_id,
        "bill_to_id": order.bill_to_id,
        "payment_terms": order.payment_terms,
        "notes": order.notes,
        "order_name": order.order_name,
        "daysmart_status_id": order.status_id,
        "daysmart_status": order.status,
        "is_active": order.is_active,
        "daysmart_business_id": order.business_id,
        "daysmart_created_by": order.created_by,
        "daysmart_updated_by": order.updated_by,
    }
    if line_total is not None:
        incoming["item_count"] = line_total       # the line list's own total
    # Only the detail surface carries these; null means "not sent", not "cleared".
    if order.supplier_name is not None:
        incoming["supplier_name"] = order.supplier_name
    if order.status_label is not None:
        incoming["daysmart_status_label"] = order.status_label
    for column, value in (("order_date", order.order_date),
                          ("daysmart_created_at", order.created_at),
                          ("daysmart_updated_at", order.updated_at)):
        if not _same_instant(getattr(po, column), value):
            incoming[column] = value.isoformat()
    for column, value in (("sub_total", order.sub_total), ("shipping", order.shipping),
                          ("tax", order.tax), ("total", order.total)):
        if not _same_money(getattr(po, column), value):
            incoming[column] = _money_str(value)

    before = po.content_hash
    for column, value in incoming.items():
        setattr(po, column, value)
    po.content_hash = _content_hash(_owned_values_from_row(po))
    po.last_seen_at = now
    if po.content_hash == before:
        return False
    po.updated_at = now
    return True


def _owned_values_from_row(po: "models.PurchaseOrder") -> dict:
    return {column: getattr(po, column) for column in OWNED_COLUMNS}


def _mirror_lines(db, daysmart, daysmart_ids: list[int],
                  failures: list[LineFailure]) -> tuple[int, int]:
    """Pull the lines of every listed order. A failure is appended to
    ``failures`` (reported) and never aborts the others."""
    if not daysmart_ids:
        return 0, 0
    rows = {po.daysmart_id: po for po in (
        db.query(models.PurchaseOrder)
        .filter(models.PurchaseOrder.daysmart_id.in_(list(daysmart_ids))).all())}
    synced = failed = 0
    for daysmart_id in daysmart_ids:
        po = rows.get(daysmart_id)
        if po is None:
            continue
        try:
            refresh_order_lines(db, po, daysmart)
            synced += 1
        except DaysmartError as exc:
            failed += 1
            failures.append(LineFailure(daysmart_id=daysmart_id,
                                        error_code=type(exc).__name__, message=str(exc)))
    return synced, failed


def _pull_receipts(db, daysmart, *, max_pages: int,
                   purchase_order_id: int | None = None) -> ReceiptPullSummary:
    """Every page of the receipt list, applied, then the rows of every
    receipt that landed on a local order. A page failure stops here and is
    reported; a row failure is reported per receipt and never aborts."""
    created = updated = unchanged = unlinked = issues = 0
    rows_synced = rows_failed = 0
    failures: list[ReceiptFailure] = []
    page_number = 0
    last_page = 1
    while page_number < last_page and page_number < max_pages:
        page_number += 1
        try:
            page = daysmart.fetch_receipts(page=page_number, purchase_order_id=purchase_order_id)
        except DaysmartError as exc:
            return ReceiptPullSummary(
                completed=False, pages_applied=page_number - 1, created=created,
                updated=updated, unchanged=unchanged, unlinked=unlinked,
                issues_recorded=issues, rows_synced=rows_synced, rows_failed=rows_failed,
                failures=tuple(failures), error=f"{type(exc).__name__}: {exc}")
        last_page = max(last_page, page.meta.last_page)
        summary = apply_receipt_page(db, page)
        created += summary.created
        updated += summary.updated
        unchanged += summary.unchanged
        unlinked += summary.unlinked
        issues += summary.issues_recorded
        # Unposted receipts name their order only on their rows: read them,
        # link, and apply in one go. A read failure is reported per receipt.
        handled: set[int] = set()
        for deferred in summary.deferred:
            handled.add(deferred.daysmart_id)
            try:
                rows = daysmart.fetch_receipt_rows(deferred.daysmart_id)
            except DaysmartError as exc:
                rows_failed += 1
                failures.append(ReceiptFailure(daysmart_receipt_id=deferred.daysmart_id,
                                               error_code=type(exc).__name__, message=str(exc)))
                continue
            outcome = link_receipt_from_rows(db, deferred, rows)
            issues += outcome.issues_recorded
            if outcome.local is None:
                unlinked += 1
            else:
                created += 1
                rows_synced += 1
        ok, failed = _mirror_receipt_rows(
            db, daysmart, [r.daysmart_id for r in page.receipts if r.daysmart_id not in handled], failures)
        rows_synced += ok
        rows_failed += failed
    return ReceiptPullSummary(
        completed=True, pages_applied=page_number, created=created, updated=updated,
        unchanged=unchanged, unlinked=unlinked, issues_recorded=issues,
        rows_synced=rows_synced, rows_failed=rows_failed, failures=tuple(failures))


def _read_receipt_pages(daysmart, *, purchase_order_id: int, max_pages: int = 50) -> list:
    """All list pages for one order — reads only, nothing applied."""
    pages = []
    page_number = 0
    last_page = 1
    while page_number < last_page and page_number < max_pages:
        page_number += 1
        page = daysmart.fetch_receipts(page=page_number, purchase_order_id=purchase_order_id)
        pages.append(page)
        last_page = max(last_page, page.meta.last_page)
    return pages


def _mirror_receipt_rows(db, daysmart, daysmart_receipt_ids: list[int],
                         failures: list[ReceiptFailure]) -> tuple[int, int]:
    if not daysmart_receipt_ids:
        return 0, 0
    local = {r.daysmart_receipt_id: r for r in (
        db.query(models.PoReceipt)
        .filter(models.PoReceipt.daysmart_receipt_id.in_(list(daysmart_receipt_ids))).all())}
    synced = failed = 0
    for rid in daysmart_receipt_ids:
        receipt = local.get(rid)
        if receipt is None:
            continue   # unlinked — not ours to mirror
        try:
            refresh_receipt_rows(db, receipt, daysmart)
            synced += 1
        except DaysmartError as exc:
            failed += 1
            failures.append(ReceiptFailure(daysmart_receipt_id=rid,
                                           error_code=type(exc).__name__, message=str(exc)))
    return synced, failed


def _record_header_issues(db, issues, now: str) -> int:
    recorded = 0
    for issue in issues:
        if _open_issue_exists(db, issue):
            continue  # already on the review screen — one row, not one per poll
        db.add(models.PoValidationIssueRecord(
            code=issue.code.value,
            field_path=issue.field_path,
            raw_value=None if issue.raw_value is None else str(issue.raw_value),
            expected=issue.expected,
            daysmart_id=issue.daysmart_id,
            payload_context="fetch",
            created_at=now,
        ))
        recorded += 1
    return recorded


def _apply_values(po: "models.PurchaseOrder", values: dict, digest: str, now: str) -> None:
    for column, value in values.items():
        setattr(po, column, value)
    po.content_hash = digest
    po.last_seen_at = now
    po.updated_at = now


def _content_hash(values: dict) -> str:
    material = json.dumps(values, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def _same_instant(stored: str | None, value: datetime) -> bool:
    if stored is None:
        return False
    try:
        parsed = datetime.fromisoformat(stored)
    except ValueError:
        return False
    return parsed.tzinfo is not None and parsed == value


def _same_money(stored: str | None, value: Decimal | None) -> bool:
    if stored is None or value is None:
        return stored is None and value is None
    try:
        return Decimal(stored) == value
    except InvalidOperation:
        return False


def _open_issue_exists(db, issue) -> bool:
    return (db.query(models.PoValidationIssueRecord)
            .filter_by(code=issue.code.value,
                       field_path=issue.field_path,
                       daysmart_id=issue.daysmart_id,
                       payload_context="fetch",
                       resolution_status="open")
            .first() is not None)


def _current_watermark(db) -> str | None:
    row = db.get(models.PoSyncWatermark, 1)
    return row.last_update_at_seen if row is not None else None


def _money_str(value) -> str | None:
    """Verbatim: None (Daysmart sent "" / null) stays NULL — unknown, never zero."""
    return None if value is None else str(value)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
