"""Push the ops-DB rows to the BizOps Google Sheet.

The sheet is a MIRROR, not a log: each push replaces the data under the header
so the tab always says what the system currently holds. Appending would let a
corrected price sit below the wrong one with nothing to say which is current.

Credentials come from the same service-account variables the supplier import
already uses (GOOGLE_SA_KEY_JSON / GOOGLE_SA_KEY_PATH), but with the read/write
scope — that import only ever reads.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass

from sqlalchemy.orm import Session

from services.ops_db_export import OPS_COLUMNS, build_published_rows

#: Read AND write. `supplier_import` asks for spreadsheets.readonly; pushing
#: needs the wider scope, so it is stated here rather than shared from there.
SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]

DEFAULT_SHEET_ID = os.environ.get("OPS_DB_SHEET_ID", "1gd87a_HWduwExB9W8zGSKjx5U8zM3ssgHo8IZAvNtwA")
DEFAULT_TAB_GID = int(os.environ.get("OPS_DB_SHEET_GID", "683712064"))


class OpsSheetError(RuntimeError):
    """Something the caller can show a person: no credential, no access, no tab."""


@dataclass(frozen=True)
class PushResult:
    rows_written: int
    columns: int
    tab: str
    sheet_id: str
    sheet_url: str


def _client(credentials_file: str | None = None):
    try:
        import gspread
        from google.oauth2.service_account import Credentials
    except ImportError as exc:  # pragma: no cover - dependency is pinned
        raise OpsSheetError("Google Sheets support is not installed on this server.") from exc

    sa_json = os.environ.get("GOOGLE_SA_KEY_JSON", "")
    path = credentials_file or os.environ.get("GOOGLE_SA_KEY_PATH", "")
    if sa_json:
        creds = Credentials.from_service_account_info(json.loads(sa_json), scopes=SCOPES)
    elif path and os.path.exists(path):
        creds = Credentials.from_service_account_file(path, scopes=SCOPES)
    else:
        raise OpsSheetError(
            "No Google service-account credential is configured on this server "
            "(GOOGLE_SA_KEY_JSON or GOOGLE_SA_KEY_PATH)."
        )
    return gspread.authorize(creds)


def _cell(value):
    """Sheets wants a bare value; the exporter hands out formatted strings."""
    if value in (None, ""):
        return ""
    text = str(value)
    # Keep numbers numeric so the sheet can compute with them, but never coerce
    # a code like "0330480090" that only looks numeric.
    try:
        if text.replace(".", "", 1).replace("-", "", 1).isdigit() and not text.startswith("0") or text in ("0", "0.0"):
            return float(text) if "." in text else int(text)
    except ValueError:
        pass
    return text


def push_published_rows(
    db: Session,
    *,
    sheet_id: str | None = None,
    tab_gid: int | None = None,
    credentials_file: str | None = None,
) -> PushResult:
    """Replace the sheet's data with today's published ops rows."""
    sheet_id = sheet_id or DEFAULT_SHEET_ID
    tab_gid = DEFAULT_TAB_GID if tab_gid is None else tab_gid

    rows = build_published_rows(db)
    gc = _client(credentials_file)
    try:
        book = gc.open_by_key(sheet_id)
    except Exception as exc:
        raise OpsSheetError(
            "Could not open the ops sheet. Check the sheet is shared with the service account."
        ) from exc

    tab = next((w for w in book.worksheets() if w.id == tab_gid), None)
    if tab is None:
        raise OpsSheetError(f"The ops sheet has no tab with id {tab_gid}.")

    payload = [list(OPS_COLUMNS)] + [[_cell(r.get(c)) for c in OPS_COLUMNS] for r in rows]
    # Clear first: a shorter run than last time would otherwise leave stale rows
    # below the new data, reading as current stock.
    tab.clear()
    tab.update(payload, "A1", value_input_option="USER_ENTERED")

    return PushResult(
        rows_written=len(rows),
        columns=len(OPS_COLUMNS),
        tab=tab.title,
        sheet_id=sheet_id,
        sheet_url=f"https://docs.google.com/spreadsheets/d/{sheet_id}/edit#gid={tab_gid}",
    )
