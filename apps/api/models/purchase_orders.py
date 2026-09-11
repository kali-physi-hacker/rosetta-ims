

from uuid import uuid4

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Column,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import relationship

from database import Base


def _uuid() -> str:
    return str(uuid4())


class PurchaseOrder(Base):
    __tablename__ = "purchase_orders"
    __table_args__ = (
        UniqueConstraint("po_uuid", name="uq_purchase_orders_po_uuid"),
        UniqueConstraint("daysmart_id", name="uq_purchase_orders_daysmart_id"),
        Index("ix_purchase_orders_sync_status", "sync_status"),
        Index("ix_purchase_orders_supplier", "supplier_daysmart_id"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    po_uuid = Column(String(36), nullable=False, default=_uuid)

    # --- Daysmart identity (write-once via assign_daysmart_identity) ------
    daysmart_id = Column(Integer, nullable=True)
    daysmart_index = Column(Integer, nullable=True)

    # --- Rosetta-owned lifecycle ------------------------------------------
    #: 'draft' | 'submitting' | 'synced' | 'sync_failed' | 'cancelled'
    #: ('cancelled' = human discarded a failed/unsent draft, local-only —
    #:  leaves the review buckets, never touches Daysmart, never retried)
    sync_status = Column(String, nullable=False, default="draft")
    created_by_user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    created_at = Column(String, nullable=False)
    updated_at = Column(String, nullable=False)

    # --- business fields ---------------------------------------------------
    supplier_daysmart_id = Column(String, nullable=False)
    supplier_name = Column(String, nullable=False)
    account_no = Column(String, nullable=False, default="")
    order_date = Column(String, nullable=False)
    ship_to_id = Column(Integer, nullable=True)
    bill_to_id = Column(Integer, nullable=True)
    payment_terms = Column(String, nullable=False, default="")
    notes = Column(String, nullable=False, default="")
    order_name = Column(String, nullable=False, default="")

    # --- money, verbatim: None means Daysmart sent "" (unknown ≠ zero) ----
    sub_total = Column(String, nullable=True)
    shipping = Column(String, nullable=True)
    tax = Column(String, nullable=True)
    total = Column(String, nullable=True)

    # --- Daysmart-owned, overwritten by fetches ---------------------------
    daysmart_status_id = Column(Integer, nullable=True)
    daysmart_status_label = Column(String, nullable=True)
    daysmart_status = Column(String, nullable=True)  # mapped: 'open' | 'closed' | NULL
    is_active = Column(Boolean, nullable=False, default=True)
    daysmart_business_id = Column(String, nullable=True)
    daysmart_created_by = Column(String, nullable=True)
    daysmart_created_at = Column(String, nullable=True)
    daysmart_updated_by = Column(String, nullable=True)
    daysmart_updated_at = Column(String, nullable=True)
    #: When a fetch last showed this order. Absence from a poll is NEVER a
    #: delete signal — Daysmart hides inactive orders from its list.
    last_seen_at = Column(String, nullable=True)
    #: Hash over the Daysmart-owned fields — cheap change detection on sync:
    #: same hash → skip the write (and the audit noise), touch last_seen_at.
    #: Equality is the ONE question hashes answer well; identity stays
    #: daysmart_id, and draft-matching stays the snapshot-diff.
    content_hash = Column(String, nullable=True)
    #: Daysmart's line count for this order, as the list surface reports it
    #: — a Daysmart-owned display value, stored and hashed like the rest.
    #: It decides NOTHING: lines are pulled with every sync and refresh,
    #: because an edited line changes no count. (A single-PO refresh keeps
    #: it in step from the line list's own total.)
    item_count = Column(Integer, nullable=True)

    lines = relationship("PurchaseOrderLine", back_populates="purchase_order",
                         cascade="all, delete-orphan")
    sync_attempts = relationship("PoSyncAttempt", back_populates="purchase_order")
    validation_issues = relationship("PoValidationIssueRecord", back_populates="purchase_order")
    receipts = relationship("PoReceipt", back_populates="purchase_order")


class PurchaseOrderLine(Base):
    """One line of a PO. Lines carry their own sync state because Daysmart's
    create is header-then-lines: a retry must know exactly which lines
    landed, or it re-adds line 1."""

    __tablename__ = "purchase_order_lines"
    __table_args__ = (
        UniqueConstraint("line_uuid", name="uq_purchase_order_lines_line_uuid"),
        Index("ix_purchase_order_lines_po", "purchase_order_id"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    line_uuid = Column(String(36), nullable=False, default=_uuid)
    purchase_order_id = Column(Integer, ForeignKey("purchase_orders.id"), nullable=False)
    daysmart_item_id = Column(Integer, nullable=True)

    #: Optional link into the product domain; a line may cite a supplier
    #: offering without one (free-typed order).
    supplier_offering_id = Column(Integer, ForeignKey("catalogue_supplier_products.id"), nullable=True)

    #: Daysmart's INVENTORY item id ("DIT-…" string) — persisted so a
    #: RESUBMIT can rebuild line payloads from the row alone (it originally
    #: lived only on the in-flight command; the retry engine exposed that).
    daysmart_inventory_item_id = Column(String, nullable=True)

    description = Column(String, nullable=False)
    quantity = Column(String, nullable=False)
    quantity_uom = Column(String, nullable=False)  # "unknown quantity ≠ 1 unit" — UOM is required
    #: Verbatim. "0.00" is a LEGAL value — free goods (11+1, buy-6-get-1)
    #: are genuinely zero-priced. Never "corrected" from item cost.
    unit_price_raw = Column(String, nullable=False)
    #: Derived display value (e.g. catalogue cost) — separate column so the
    #: raw observation is never overwritten.
    display_cost = Column(String, nullable=True)
    notes = Column(String, nullable=False, default="")

    #: 'pending' | 'synced' | 'sync_failed' | 'needs_review' | 'deleted'
    #: ('deleted' = removed in Daysmart through Rosetta; the row stays for audit)
    sync_status = Column(String, nullable=False, default="pending")

    # --- Daysmart-owned mirror (overwritten by the lines inbox; the
    # Rosetta-owned fields above are what WE ordered and are never touched
    # by a fetch — same ownership split as the PO header) -----------------
    daysmart_item_name = Column(String, nullable=True)
    daysmart_quantity_ordered = Column(Integer, nullable=True)
    daysmart_quantity_received = Column(Integer, nullable=True)
    daysmart_back_ordered = Column(Integer, nullable=True)
    #: The wire price VERBATIM — it arrives as a float with repeating
    #: decimals (their line_total / quantity); never rounded here.
    daysmart_price_raw = Column(String, nullable=True)
    daysmart_line_total_raw = Column(String, nullable=True)
    daysmart_container_type = Column(String, nullable=True)
    daysmart_catalogue_cost_raw = Column(String, nullable=True)
    #: Mirrored, never inferred: absence from a fetch is not deactivation.
    daysmart_is_active = Column(Boolean, nullable=True)
    daysmart_updated_at = Column(String, nullable=True)
    content_hash = Column(String, nullable=True)
    last_seen_at = Column(String, nullable=True)

    purchase_order = relationship("PurchaseOrder", back_populates="lines")


class PoSyncAttempt(Base):
    """Append-only log of every Daysmart interaction for a PO.

    This is what retry-by-error-type reads: the last outcome for a PO
    decides whether the next trigger may simply re-send (``daysmart_down``),
    must snapshot-diff first (``timeout``), or must leave it to a human
    (``contract_mismatch``). Rows are never updated or deleted.
    """

    __tablename__ = "po_sync_attempts"
    __table_args__ = (
        Index("ix_po_sync_attempts_po", "purchase_order_id"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    purchase_order_id = Column(Integer, ForeignKey("purchase_orders.id"), nullable=False)
    line_id = Column(Integer, ForeignKey("purchase_order_lines.id"), nullable=True)
    #: The receipt this attempt was about (create/rows/update/post steps) —
    #: lets a receipt retry read ITS last outcome, not a sibling's.
    receipt_id = Column(Integer, ForeignKey("po_receipts.id"), nullable=True)

    #: PO and receipt wire steps, including create_header/add_line/fetch,
    #: create_receipt/fetch_receipt_items/update_receipt_item/post_receipt.
    step = Column(String, nullable=False)
    #: 'success' | 'daysmart_down' | 'timeout' | 'contract_mismatch' | 'auth_failure'
    outcome = Column(String, nullable=False)
    error_code = Column(String, nullable=True)
    error_detail = Column(String, nullable=True)
    started_at = Column(String, nullable=False)
    finished_at = Column(String, nullable=False)

    purchase_order = relationship("PurchaseOrder", back_populates="sync_attempts")


class PoValidationIssueRecord(Base):
    """A persisted contract violation (``schemas.purchase_orders`` issue).

    Feeds the OPS review screen. ``purchase_order_id`` is nullable on
    purpose: an inbound order that fails the contract may not be linkable to
    any local PO — not linkable must never mean not recorded.
    """

    __tablename__ = "po_validation_issues"
    __table_args__ = (
        Index("ix_po_validation_issues_status", "resolution_status"),
        Index("ix_po_validation_issues_po", "purchase_order_id"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    code = Column(String, nullable=False)
    field_path = Column(String, nullable=False)
    raw_value = Column(String, nullable=True)
    expected = Column(String, nullable=False, default="")
    daysmart_id = Column(Integer, nullable=True)
    purchase_order_id = Column(Integer, ForeignKey("purchase_orders.id"), nullable=True)
    #: 'fetch' | 'create_response' | 'resolve' | 'diff'
    payload_context = Column(String, nullable=False)
    resolution_status = Column(String, nullable=False, default="open")
    created_at = Column(String, nullable=False)
    resolved_at = Column(String, nullable=True)
    resolved_by = Column(String, nullable=True)

    purchase_order = relationship("PurchaseOrder", back_populates="validation_issues")


class PoReceipt(Base):
    """A receiving receipt against a PO — local-first, like the PO itself.

    Two independent lifecycles, deliberately separate columns:
    - ``sync_status``: does Daysmart have this receipt? (draft →
      submitting → synced | sync_failed, same vocabulary as the PO)
    - ``post_status``: has it been committed to stock? ('draft' |
      'posted') — posting is Daysmart's point of no return and is always
      an EXPLICIT step, never a side effect of syncing.
    """

    __tablename__ = "po_receipts"
    __table_args__ = (
        UniqueConstraint("receipt_uuid", name="uq_po_receipts_receipt_uuid"),
        UniqueConstraint("daysmart_receipt_id", name="uq_po_receipts_daysmart_id"),
        Index("ix_po_receipts_po", "purchase_order_id"),
        Index("ix_po_receipts_sync_status", "sync_status"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    receipt_uuid = Column(String(36), nullable=False, default=_uuid)
    purchase_order_id = Column(Integer, ForeignKey("purchase_orders.id"), nullable=False)
    #: Write-once from the create ack; nullable until synced, unique after.
    daysmart_receipt_id = Column(Integer, nullable=True)

    sync_status = Column(String, nullable=False, default="draft")
    post_status = Column(String, nullable=False, default="draft")

    invoice_number = Column(String, nullable=False, default="")
    receipt_date = Column(String, nullable=False)
    #: Server-computed by Daysmart from the items (sent as null on create);
    #: stored verbatim when learned from an update echo.
    amount = Column(String, nullable=True)
    shipping = Column(String, nullable=True)
    tax = Column(String, nullable=True)
    tax_type = Column(Integer, nullable=True)
    notes = Column(String, nullable=False, default="")

    # --- Daysmart-owned mirror (overwritten by the receipt pull; the
    # fields above are what WE sent, seeded once for an imported receipt
    # and never touched by a pull — the same split as PO headers) --------
    daysmart_index = Column(Integer, nullable=True)
    daysmart_status_id = Column(Integer, nullable=True)
    daysmart_status_label = Column(String, nullable=True)
    #: 'in_process' | 'posted' | NULL — the receipt vocabulary, not the order one.
    daysmart_status = Column(String, nullable=True)
    daysmart_receipt_date = Column(String, nullable=True)
    daysmart_post_at = Column(String, nullable=True)
    daysmart_invoice_number = Column(String, nullable=True)
    daysmart_amount = Column(String, nullable=True)
    daysmart_tax = Column(String, nullable=True)
    daysmart_shipping = Column(String, nullable=True)
    daysmart_tax_type = Column(Integer, nullable=True)
    daysmart_notes = Column(String, nullable=True)
    daysmart_is_active = Column(Boolean, nullable=True)
    #: Daysmart's own link; null after their soft delete (ours is kept).
    daysmart_purchase_order_id = Column(Integer, nullable=True)
    daysmart_supplier_id = Column(String, nullable=True)
    daysmart_supplier_name = Column(String, nullable=True)
    daysmart_item_count = Column(Integer, nullable=True)
    daysmart_created_at = Column(String, nullable=True)
    daysmart_updated_at = Column(String, nullable=True)
    content_hash = Column(String, nullable=True)
    last_seen_at = Column(String, nullable=True)

    created_by_user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    created_at = Column(String, nullable=False)
    updated_at = Column(String, nullable=False)

    purchase_order = relationship("PurchaseOrder", back_populates="receipts")
    items = relationship("PoReceiptItem", back_populates="receipt",
                         cascade="all, delete-orphan")


class PoReceiptItem(Base):
    """One received item. DaySmart auto-creates it from the selected PO;
    Rosetta then updates that generated row by id. Partial sync is a natural
    state, so each item carries its own ``sync_status``.

    ``quantity_received`` and ``quantity_in_stock`` are separate verbatim
    strings: short-shipping is representable and never collapsed.
    """

    __tablename__ = "po_receipt_items"
    __table_args__ = (
        UniqueConstraint("item_uuid", name="uq_po_receipt_items_item_uuid"),
        Index("ix_po_receipt_items_receipt", "po_receipt_id"),
        Index("uq_po_receipt_items_daysmart_item",
              "daysmart_receipt_item_id", unique=True),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    item_uuid = Column(String(36), nullable=False, default=_uuid)
    po_receipt_id = Column(Integer, ForeignKey("po_receipts.id"), nullable=False)
    #: The auto-created DaySmart receipt-item id. Learned by fetching the
    #: receipt immediately after header creation, then used for PUT updates.
    daysmart_receipt_item_id = Column(Integer, nullable=True)
    #: Our PO line, when known.
    purchase_order_line_id = Column(Integer, ForeignKey("purchase_order_lines.id"), nullable=True)
    #: Daysmart's PO line id (item payload's purchase_order_item_id) —
    #: required for the item to be SENDABLE; never guessed.
    daysmart_po_item_id = Column(Integer, nullable=True)
    #: Daysmart inventory item the stock posts to — also required to send.
    #: A STRING id ("DIT-…" prefixed), wire-proven; not an integer.
    post_to_item_id = Column(String, nullable=True)

    quantity_received = Column(String, nullable=False)
    quantity_in_stock = Column(String, nullable=False)
    amount = Column(String, nullable=False)
    expiration_date = Column(String, nullable=True)
    lot_number = Column(String, nullable=False, default="")
    manufacturer = Column(String, nullable=True)
    ndc_code = Column(String, nullable=True)
    tax = Column(String, nullable=True)

    sync_status = Column(String, nullable=False, default="pending")

    # --- Daysmart-owned mirror (overwritten by the receipt pull) ---------
    daysmart_quantity_received = Column(Integer, nullable=True)
    daysmart_quantity_in_stock = Column(Integer, nullable=True)
    daysmart_quantity_rejected = Column(Integer, nullable=True)
    daysmart_amount_raw = Column(String, nullable=True)
    daysmart_tax_raw = Column(String, nullable=True)
    daysmart_lot_number = Column(String, nullable=True)
    #: Epoch seconds, verbatim.
    daysmart_expiration_date = Column(Integer, nullable=True)
    daysmart_manufacturer = Column(String, nullable=True)
    daysmart_ndc_code = Column(String, nullable=True)
    daysmart_status_id = Column(Integer, nullable=True)
    daysmart_is_active = Column(Boolean, nullable=True)
    #: The inventory item Daysmart posts this row to (its ``item_id``); the
    #: Rosetta-owned ``post_to_item_id`` above is what WE want.
    daysmart_post_to_item_id = Column(String, nullable=True)
    daysmart_updated_at = Column(String, nullable=True)
    content_hash = Column(String, nullable=True)
    last_seen_at = Column(String, nullable=True)

    receipt = relationship("PoReceipt", back_populates="items")


class PoRequestIdempotency(Base):
    """Durable idempotency record for the PO/receipt CREATION endpoints.

    Same convention as ``CatalogueSubmissionIdempotency``: the client sends
    an ``Idempotency-Key`` header; the same key with the same material
    fingerprint replays the stored response (no second PO, no second
    Daysmart order); the same key with DIFFERENT material is a 409. A
    replayed failure is NOT retried — retry belongs to the sync flow, not
    to a repeated POST.
    """

    __tablename__ = "po_request_idempotency"
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_po_request_idempotency_key"),
        CheckConstraint("length(trim(idempotency_key)) > 0",
                        name="ck_po_idempotency_key_not_blank"),
        CheckConstraint("length(material_fingerprint) = 64",
                        name="ck_po_idempotency_fingerprint_sha256"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    idempotency_key = Column(String, nullable=False)
    #: sha256 over (endpoint, canonical JSON body) — one key, one material.
    material_fingerprint = Column(String(64), nullable=False)
    endpoint = Column(String, nullable=False)
    response_json = Column(String, nullable=False)
    created_at = Column(String, nullable=False)


class PoSyncWatermark(Base):
    """Single-row bookmark: the highest Daysmart ``update_at`` fully
    processed. Managed exclusively through ``services.po_sync_state`` —
    which is where "never moves backwards" is enforced."""

    __tablename__ = "po_sync_watermark"

    id = Column(Integer, primary_key=True, autoincrement=True)
    last_update_at_seen = Column(String, nullable=False)
    updated_at = Column(String, nullable=False)
