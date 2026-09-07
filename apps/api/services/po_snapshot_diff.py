

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from schemas.purchase_orders import DaysmartOrderV1


@dataclass(frozen=True)
class DiffVerdict:
    kind: str  # 'matched' | 'not_landed' | 'needs_review'
    order: DaysmartOrderV1 | None = None
    candidates: tuple[DaysmartOrderV1, ...] = ()
    reason: str = ""


def resolve_snapshot_diff(
    *,
    snapshot_max_daysmart_id: int,
    supplier_daysmart_id: str,
    submitted_between: tuple[datetime, datetime],
    expected_line_count: int,
    candidates: list[DaysmartOrderV1],
    page_had_issues: bool = False,
) -> DiffVerdict:
    window_start, window_end = submitted_between

    in_window = [
        order for order in candidates
        if order.daysmart_id > snapshot_max_daysmart_id
        and order.supplier.daysmart_id == supplier_daysmart_id
        and window_start <= order.created_at <= window_end
    ]
    plausible = [o for o in in_window if o.item_count <= expected_line_count]
    implausible = [o for o in in_window if o.item_count > expected_line_count]

    if len(plausible) == 1 and not implausible:
        return DiffVerdict(kind="matched", order=plausible[0],
                           candidates=tuple(plausible),
                           reason="exactly one plausible new order for this supplier "
                                  "in the submission window")

    if not in_window:
        if page_had_issues:
            return DiffVerdict(
                kind="needs_review",
                reason="no match found, but the diff page had parse issues — an "
                       "unparseable order could be ours, so absence proves nothing; "
                       "do not resend without a human decision")
        return DiffVerdict(kind="not_landed",
                           reason="no new order for this supplier in the window — "
                                  "the create did not land; safe to resend")

    if implausible and not plausible:
        return DiffVerdict(
            kind="needs_review", candidates=tuple(in_window),
            reason="a same-supplier order appeared in our window carrying more "
                   "lines than the draft ever had — not ours, but too close a "
                   "coincidence to resend on autopilot")

    return DiffVerdict(
        kind="needs_review", candidates=tuple(in_window),
        reason=f"{len(in_window)} candidate orders are plausible — auto-linking "
               "would be a guess; a human picks (or rejects) one")