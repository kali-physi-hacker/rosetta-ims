"""Named validation issues for the purchase-order contracts (DEV-305).

The ticket's rule: a contract mismatch is a NAMED VALIDATION ISSUE, not an
exception. Shape problems travel as data — ``(order | None, issues)`` — so
one bad order can never poison a batch, and every failure carries enough to
answer "what field, what raw value, which order" without a debugger.

Exceptions stay reserved for transport failures (Daysmart unreachable, auth
dead) — situations with no payload to attach an issue to.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any


class IssueCode(str, Enum):
    """Closed vocabulary of purchase-order contract issue codes.

    Closed means closed: new failure modes get a new member here (and a
    test), never an ad-hoc string at an emission site.
    """

    #: A money field holds something that is neither "" nor a plain decimal
    #: string. Blocking — a PO with unreadable money must not be trusted.
    PO_MONEY_UNPARSEABLE = "PO_MONEY_UNPARSEABLE"

    #: A field the contract relies on is absent from the payload. Blocking.
    PO_FIELD_MISSING = "PO_FIELD_MISSING"

    #: A field is present but its type/shape is not the observed contract
    #: (status as a string, id as a word, naive datetime …). Blocking.
    PO_FIELD_INVALID = "PO_FIELD_INVALID"

    #: A status id outside the observed vocabulary {1: Open, 4: Closed}.
    #: Non-blocking: the order is still usable (linking, display) but its
    #: mapped status is explicitly unknown until a human classifies the id.
    PO_STATUS_UNKNOWN = "PO_STATUS_UNKNOWN"

    #: A known status id arrived with an unexpected label — the cheapest
    #: early warning that Daysmart's status surface is drifting. Non-blocking.
    PO_STATUS_LABEL_DRIFT = "PO_STATUS_LABEL_DRIFT"

    #: An OPTIONAL field arrived with the wrong shape (e.g. ship_to_id as a
    #: string). Non-blocking: the order is still usable and the value is
    #: withheld (None) — but the drift is recorded instead of silently
    #: nulling data fleet-wide, which is how it originally slipped through.
    PO_OPTIONAL_FIELD_INVALID = "PO_OPTIONAL_FIELD_INVALID"


#: Codes that leave the parsed order usable (issue attached, order returned).
NON_BLOCKING_CODES = frozenset({
    IssueCode.PO_STATUS_UNKNOWN,
    IssueCode.PO_STATUS_LABEL_DRIFT,
    IssueCode.PO_OPTIONAL_FIELD_INVALID,
})


@dataclass(frozen=True)
class PoValidationIssue:
    """One named contract violation, carrying its evidence."""

    code: IssueCode
    field_path: str
    raw_value: Any = None
    expected: str = ""
    #: Identity of the offending order, when the payload still reveals one.
    daysmart_id: int | None = None

    @property
    def blocking(self) -> bool:
        return self.code not in NON_BLOCKING_CODES
