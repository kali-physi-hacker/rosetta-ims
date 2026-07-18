# -*- coding: utf-8 -*-
"""Standardised naming for Klaviyo lists & flows, applied in the SSOT (display layer) so names are clean
and SELF-JOINING even while Seph finishes the renames in Klaviyo itself.

Convention (Chris + Seph aligned 2026-06-23):  [TYPE] - [CHANNEL] - [CAMPAIGN/CODE]
  TYPE     = LIST (onboarding/consent capture) or FLOW (outreach to onboarded)
  CHANNEL  = WA | SITE | META | QUIZ | B2B | AUTOSHIP | ALL | SYS
  CAMPAIGN = the shared token that links a list to the flow it feeds (DHVH, GIFT100, CONSULT, …)
A list and the flow it triggers share [CHANNEL]-[CAMPAIGN] -> the link is self-evident
(e.g. LIST - WA - DHVH  <->  FLOW - WA - DHVH). Already-canonical names pass through; raw Klaviyo
IDs and unmapped names are left as-is."""
import re

LIST_MAP = {
    "[master] email subscribers": "LIST - ALL - MASTER",
    "pp_gift100_claimed [list only]": "LIST - SITE - GIFT100",
    "dhvh/ohana whatsapp blast (ultimate bundle)": "LIST - WA - DHVH",
    "petproject - consult claims": "LIST - WA - CONSULT",
    "quiz submission (consult)": "LIST - QUIZ - CONSULT",
    "whatsapp subscribers": "LIST - WA - SUBSCRIBERS",
    "b2b prospects": "LIST - B2B - PROSPECTS",
    "b2b consult claim": "LIST - B2B - CONSULT",
}
FLOW_MAP = {
    "gift100 [pop-up] - gift4dog/cat [list trigger]": "FLOW - SITE - GIFT100",
    "gift100 [meta form] - alohadog/cat": "FLOW - META - GIFT100",
    "gift100 [dhvh-whatsapp] - gift4dog/cat": "FLOW - WA - DHVH",
    "gift100 claim [on site] - gift4dog/cat": "FLOW - SITE - GIFT100-FOOTER",
    "site abandonment": "FLOW - SITE - ABANDON",
    "quiz - consult claim": "FLOW - QUIZ - CONSULT",
    "dhvh - consult claim (internal notification)": "FLOW - WA - CONSULT-ALERT",
    "vetcare benefit form claim slack notification": "FLOW - B2B - CONSULT-ALERT",
    "for exclusion": "FLOW - SYS - BOUNCE-EXCLUDE",
    "post purchase": "FLOW - ORDER - POSTPURCHASE",
    "lapsed customers flow": "FLOW - WINBACK - LAPSED",
}
for _i, _name in [(1, "WELCOME"), (2, "UPCOMING"), (3, "SCHEDULE"), (4, "SKIP"), (5, "GIFT-PREVIEW"), (6, "GIFT-SHIPS"), (7, "LOYALTY")]:
    FLOW_MAP[f"pp | autoship | email {_i}"] = f"FLOW - AUTOSHIP - E{_i}-{_name}"

_CANON = re.compile(r"^(flow|list|browse)\s*-\s*", re.I)

def canonical(raw, kind):
    """kind: 'list' or 'flow'. Returns the standardised name (or the raw name if already canonical/unknown)."""
    if not raw:
        return raw
    k = raw.strip().lower()
    table = LIST_MAP if kind == "list" else FLOW_MAP
    if k in table:
        return table[k]
    if kind == "flow":  # autoship names carry an em-dash suffix; match on the prefix
        for pre, val in FLOW_MAP.items():
            if pre.startswith("pp | autoship | email") and k.startswith(pre):
                return val
    if _CANON.match(k):              # Seph already renamed it in Klaviyo -> keep
        return raw.strip()
    return raw.strip()               # raw Klaviyo IDs / unmapped -> leave untouched
