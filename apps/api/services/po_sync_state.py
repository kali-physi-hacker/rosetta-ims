"""Sync-state invariants for purchase orders (CIS-09, phase 2).

Two invariants live here rather than in the models, because both are rules
about *transitions*, not about single rows:

- The watermark only moves forward. An out-of-order or replayed page must
  never rewind the bookmark — that would re-read weeks of history or, on
  the next advance, skip changes that arrived in between.
- Daysmart identity is write-once. Once a local PO is linked to a Daysmart
  order, relinking it to a DIFFERENT order is refused: that situation means
  the matching logic went wrong somewhere, and unlinking/relinking is a
  human decision (the OPS resolve endpoint), never a silent write.

Both helpers only STAGE changes (no commit) — the caller owns the
transaction, same convention as ``services.audit``.
"""

from __future__ import annotations

from datetime import datetime, timezone

import models


class DaysmartIdentityConflict(ValueError):
    """A PO already linked to one Daysmart order was asked to link to another."""


def assign_daysmart_identity(po: "models.PurchaseOrder", *, daysmart_id: int,
                             daysmart_index: int | None) -> None:
    """Link a local PO to its Daysmart order, exactly once.

    Idempotent for the same identity (re-processing a create response or a
    resolve must be safe to repeat). Refuses a different identity.
    """
    if po.daysmart_id is not None and po.daysmart_id != daysmart_id:
        raise DaysmartIdentityConflict(
            f"PO {po.po_uuid} is already linked to Daysmart order "
            f"{po.daysmart_id}; refusing to relink to {daysmart_id}. "
            "Unlink deliberately via the OPS resolve flow instead."
        )
    po.daysmart_id = daysmart_id
    if po.daysmart_index is None:
        po.daysmart_index = daysmart_index


def get_watermark(db) -> str | None:
    """The highest Daysmart ``update_at`` fully processed, or None if the
    sync has never run (initial import state)."""
    row = db.get(models.PoSyncWatermark, 1)
    return row.last_update_at_seen if row is not None else None


def advance_watermark(db, value: str) -> str | None:
    """Move the bookmark forward to ``value``. Returns the effective
    watermark after the call.

    Rules (each earned by a review finding):
    - The row is PINNED to id=1 — single-row by construction, so a race of
      two first-advances converges on one row instead of forking.
    - An invalid CANDIDATE (unparseable, or naive) never advances — a
      malformed value must not poison the bookmark.
    - An invalid STORED value (unparseable/naive, e.g. a bad manual seed)
      is SELF-HEALED by a valid candidate — previously the aware-vs-naive
      comparison raised TypeError and crashed every subsequent sync.
    - Otherwise: forward moves apply, backward moves are silent no-ops.
    """
    candidate = _parse_aware(value)
    row = db.get(models.PoSyncWatermark, 1)
    if candidate is None:
        return row.last_update_at_seen if row is not None else None

    now = datetime.now(timezone.utc).isoformat()
    if row is None:
        db.add(models.PoSyncWatermark(id=1, last_update_at_seen=value, updated_at=now))
        return value

    stored = _parse_aware(row.last_update_at_seen)
    if stored is not None and candidate <= stored:
        return row.last_update_at_seen
    row.last_update_at_seen = value
    row.updated_at = now
    return value


def _parse_aware(value: str):
    """The tz-aware datetime, or None for anything unparseable or naive."""
    try:
        parsed = datetime.fromisoformat(value)
    except (ValueError, TypeError):
        return None
    return parsed if parsed.tzinfo is not None and parsed.utcoffset() is not None else None
