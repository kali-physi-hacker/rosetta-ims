"""Outbound mapper: Rosetta command → Daysmart create payload.

The ONE place that knows Daysmart's write shapes. The header payload below
reproduces the HAR-proven spike model (``CreateOrderRequest``) exactly —
same keys, same JSON types — because the browser API is type-strict in
undocumented ways (the working capture sent int address ids and an
epoch-int ``order_date``, so we do too).

Known asymmetry, owned here and nowhere else: Daysmart RETURNS ISO-8601
``+08:00`` strings but ACCEPTS an epoch integer on create.
"""

from __future__ import annotations

from typing import Any, Sequence

from .commands_v1 import (
    CreatePurchaseOrderCommandV1,
    CreatePurchaseOrderLineCommandV1,
    UpdatePurchaseOrderCommandV1,
)


def build_daysmart_create_payload(
    command: CreatePurchaseOrderCommandV1,
    *,
    default_ship_to_id: int,
    default_bill_to_id: int,
) -> dict[str, Any]:
    """The header-create JSON for ``POST /barramundi/inventory/order/add``.

    Header ONLY — create is header-then-lines; line data never rides here.
    Explicit addresses on the command win; the configured clinic defaults
    fill the gaps.
    """
    return {
        "supplier_id": command.supplier_daysmart_id,
        "supplier_name": command.supplier_name,
        "account_no": command.account_no,
        "order_date": int(command.order_date.timestamp()),
        "ship_to_id": command.ship_to_id if command.ship_to_id is not None else default_ship_to_id,
        "bill_to_id": command.bill_to_id if command.bill_to_id is not None else default_bill_to_id,
        # The captured working request sent null (not "") for empty notes;
        # mirror the wire exactly — account_no, by contrast, was sent as "".
        "notes": command.notes or None,
        "template_id": command.template_id,
    }


def build_daysmart_line_payloads(
    daysmart_order_id: int,
    lines: Sequence[CreatePurchaseOrderLineCommandV1],
    *,
    daysmart_item_ids: Sequence[int | None] | None = None,
) -> list[dict[str, Any]]:
    """The array body for ``POST /barramundi/inventory/order/item/save``.

    Source-observed shape (their UI builds exactly these keys). Only lines
    carrying a ``daysmart_inventory_item_id`` are sendable — the payload
    requires Daysmart's ``item_id``, and we never guess one; id-less lines
    stay local as 'pending' for OPS to complete.

    ``daysmart_item_ids`` (parallel to ``lines``) turns entries into
    UPDATES: save is an upsert, and ``purchase_order_item_id`` is what
    flips it — include it only when the Daysmart line id is known.

    Money goes as EXACT decimal strings (their UI sends floats computed as
    line_total/quantity; we refuse float arithmetic — surface-doc probe #2
    confirms acceptance on first live use). ``line_total`` is quantity ×
    unit price, exact.
    """
    payloads: list[dict[str, Any]] = []
    for i, line in enumerate(lines):
        if line.daysmart_inventory_item_id is None:
            continue
        line_total = line.unit_price * line.quantity
        payload: dict[str, Any] = {
            "purchase_order_id": daysmart_order_id,
            "item_id": line.daysmart_inventory_item_id,
            "item_name": line.description,
            "quantity_ordered": line.quantity,
            "price": str(line.unit_price),
            "line_total": str(line_total),
            "notes": line.notes,
        }
        existing_id = daysmart_item_ids[i] if daysmart_item_ids is not None else None
        if existing_id is not None:
            payload["purchase_order_item_id"] = existing_id
        payloads.append(payload)
    return payloads

def build_daysmart_update_payload(
    command: UpdatePurchaseOrderCommandV1,
    *,
    ship_to_id: int,
    bill_to_id: int,
) -> dict[str, Any]:
    """The header-update JSON for ``PUT /barramundi/inventory/order/{id}`` —
    WIRE (PO-update.har): ``{account_no, order_date:<epoch int>, ship_to_id,
    bill_to_id, notes}``, byte-for-byte the captured request."""
    return {
        "account_no": command.account_no,
        "order_date": int(command.order_date.timestamp()),
        "ship_to_id": ship_to_id,
        "bill_to_id": bill_to_id,
        "notes": command.notes,
    }
