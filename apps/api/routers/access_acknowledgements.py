"""Click-wrap NDA acknowledgement endpoints for /tech-stack.

On submit the system both (a) records the row in the database AND (b) sends
an email to chris@algogroup.io with the requestor's email cc'd — creating a
multi-party paper trail (DB row + Chris's inbox + requestor's inbox).
"""
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session

import database
import models
from dependencies import require_user, require_admin
from services import email_service, audit_log

router = APIRouter(prefix="/access-acknowledgements", tags=["access-acknowledgements"])

CURRENT_TERMS_VERSION = "v1-2026-06"


class AcknowledgementCreate(BaseModel):
    github_username: str
    full_name_typed: str
    email_requestor: str


def _row_dict(a: models.AccessAcknowledgement, user: Optional[models.User] = None) -> dict:
    return {
        "id":               a.id,
        "user_id":          a.user_id,
        "user_display":     user.display_name if user else None,
        "github_username":  a.github_username,
        "full_name_typed":  a.full_name_typed,
        "email_requestor":  a.email_requestor,
        "terms_version":    a.terms_version,
        "ip_address":       a.ip_address,
        "accepted_at":      a.accepted_at,
        "email_sent_at":    a.email_sent_at,
        "email_send_error": a.email_send_error,
    }


@router.get("/me")
def get_my_acknowledgement(
    user: models.User = Depends(require_user),
    db: Session = Depends(database.get_db),
):
    """Has the current user already submitted an access request under the current terms?"""
    row = (db.query(models.AccessAcknowledgement)
             .filter(models.AccessAcknowledgement.user_id == user.id)
             .filter(models.AccessAcknowledgement.terms_version == CURRENT_TERMS_VERSION)
             .order_by(models.AccessAcknowledgement.accepted_at.desc())
             .first())
    if not row:
        return {"acknowledged": False, "current_terms_version": CURRENT_TERMS_VERSION}
    return {
        "acknowledged": True,
        "current_terms_version": CURRENT_TERMS_VERSION,
        "acknowledgement": _row_dict(row),
    }


@router.post("")
def create_acknowledgement(
    body: AcknowledgementCreate,
    request: Request,
    user: models.User = Depends(require_user),
    db: Session = Depends(database.get_db),
):
    """Record an NDA acceptance + send an email to chris@algogroup.io with the requestor cc'd."""
    github = body.github_username.strip().lstrip("@")
    name   = body.full_name_typed.strip()
    email  = body.email_requestor.strip().lower()

    if not github:
        raise HTTPException(status_code=400, detail="GitHub username required")
    if not name or len(name) < 2:
        raise HTTPException(status_code=400, detail="Please type your full name")
    if not email or "@" not in email or "." not in email.split("@")[-1]:
        raise HTTPException(status_code=400, detail="Please enter a valid email address")

    ip = request.client.host if request.client else None
    fwd = request.headers.get("x-forwarded-for")
    if fwd:
        ip = fwd.split(",")[0].strip()

    now = datetime.now(timezone.utc).isoformat()

    row = models.AccessAcknowledgement(
        user_id=user.id,
        github_username=github,
        full_name_typed=name,
        email_requestor=email,
        terms_version=CURRENT_TERMS_VERSION,
        ip_address=ip,
        accepted_at=now,
    )
    db.add(row)
    db.commit()
    db.refresh(row)

    # Send the email — non-fatal if it fails, the row is already saved.
    ok, err = email_service.send_access_request_email(
        full_name=name,
        github_username=github,
        requestor_email=email,
        ims_user_display=user.display_name,
        ip_address=ip,
        accepted_at=now,
        terms_version=CURRENT_TERMS_VERSION,
    )
    if ok:
        row.email_sent_at = datetime.now(timezone.utc).isoformat()
    else:
        row.email_send_error = err
    db.commit()
    db.refresh(row)

    audit_log.record(db, action="access.acknowledge", actor=user,
                     entity_type="access_acknowledgement", entity_id=row.id,
                     entity_label=f"{name} ({github})",
                     details={"github": github, "email_requestor": email,
                              "terms_version": CURRENT_TERMS_VERSION,
                              "email_sent": bool(row.email_sent_at)}, request=request, commit=True)

    return {
        "acknowledgement": _row_dict(row, user),
        "email_sent":      bool(row.email_sent_at),
        "email_error":     row.email_send_error,
    }


@router.get("")
def list_acknowledgements(
    admin: models.User = Depends(require_admin),
    db: Session = Depends(database.get_db),
):
    """Admin only — list all acknowledgements (paper trail audit)."""
    rows = (db.query(models.AccessAcknowledgement)
              .order_by(models.AccessAcknowledgement.accepted_at.desc())
              .all())
    users = {u.id: u for u in db.query(models.User).all()}
    return {"acknowledgements": [_row_dict(r, users.get(r.user_id)) for r in rows]}
