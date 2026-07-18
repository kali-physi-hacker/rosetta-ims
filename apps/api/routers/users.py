"""User-account management — Admin only. Every change is audited."""
import os
import secrets
import threading
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session

import database
import models
from dependencies import hash_password
from permissions import require_capability, VALID_ROLES, ROLE_ADMIN, ROLE_LABELS
from services import audit_log, email_service

router = APIRouter(prefix="/users", tags=["users"])

APP_URL = os.environ.get("APP_URL", "https://rosetta-ims.vercel.app").rstrip("/")
INVITE_DAYS = 7


def _invite_status(u: models.User) -> str:
    if u.invite_token and not u.invite_accepted_at:
        return "invited"
    return "active" if u.is_active else "inactive"


def _user_dict(u: models.User) -> dict:
    return {
        "id":            u.id,
        "username":      u.username,
        "display_name":  u.display_name,
        "email":         u.email,
        "role":          u.role,
        "role_label":    ROLE_LABELS.get(u.role, u.role),
        "is_active":     bool(u.is_active),
        "invite_status": _invite_status(u),
        "invited_by":    u.invited_by,
        "invite_expires_at": u.invite_expires_at,
        "created_at":    u.created_at,
        "updated_at":    u.updated_at,
        "last_login_at": u.last_login_at,
    }


@router.get("")
def list_users(db: Session = Depends(database.get_db),
               _: models.User = Depends(require_capability("user_admin"))):
    users = db.query(models.User).order_by(models.User.is_active.desc(), models.User.username).all()
    return {"users": [_user_dict(u) for u in users], "roles": list(VALID_ROLES)}


def _issue_invite(user: models.User) -> str:
    token = secrets.token_urlsafe(32)
    user.invite_token = token
    user.invite_expires_at = (datetime.utcnow() + timedelta(days=INVITE_DAYS)).isoformat()
    return f"{APP_URL}/onboard?token={token}"


def _send_invite_async(email: str, url: str, role_label: str, invited_by: str) -> None:
    """Fire-and-forget the invite email so the HTTP request never blocks on SMTP. The
    copy-able invite link is always returned to the admin as the reliable fallback."""
    def _run():
        try:
            email_service.send_invite_email(email, url, role_label, invited_by, INVITE_DAYS)
        except Exception:
            pass
    threading.Thread(target=_run, daemon=True, name="invite-email").start()


class InviteUser(BaseModel):
    email: str
    role: str = "bizops"
    display_name: str = ""


@router.post("/invite")
def invite_user(body: InviteUser, request: Request,
                db: Session = Depends(database.get_db),
                admin: models.User = Depends(require_capability("user_admin"))):
    """Invite a user by email. Creates a pending (inactive) account + emails an onboarding
    link where they set their own username, name, email and password."""
    email = body.email.strip().lower()
    if not email or "@" not in email:
        raise HTTPException(status_code=400, detail="A valid email address is required")
    if body.role not in VALID_ROLES:
        raise HTTPException(status_code=400, detail=f"Invalid role: {body.role}")

    existing = db.query(models.User).filter(models.User.email == email).first()
    if existing and existing.invite_accepted_at is None and existing.invite_token:
        user = existing                      # pending invite for this email → refresh it
        user.role = body.role
        if body.display_name.strip():
            user.display_name = body.display_name.strip()
    elif existing:
        raise HTTPException(status_code=409, detail="A user with this email already exists")
    else:
        now = datetime.utcnow().isoformat()
        user = models.User(
            username=f"pending-{secrets.token_hex(5)}",   # placeholder; replaced on accept
            display_name=(body.display_name.strip() or email.split("@")[0]),
            email=email,
            password_hash=hash_password(secrets.token_urlsafe(24)),  # unusable until they set one
            role=body.role,
            is_active=0,
            created_at=now,
        )
        db.add(user)

    invite_url = _issue_invite(user)
    user.invited_by = admin.display_name
    user.updated_at = datetime.utcnow().isoformat()
    db.flush()
    audit_log.record(db, action="user.invite", actor=admin, entity_type="user",
                     entity_id=user.id, entity_label=email, details={"role": body.role},
                     request=request)
    db.commit()
    db.refresh(user)
    _send_invite_async(email, invite_url, ROLE_LABELS.get(body.role, body.role), admin.display_name)
    return {**_user_dict(user), "invite_url": invite_url, "email_status": "sending"}


@router.post("/{user_id}/resend-invite")
def resend_invite(user_id: int, request: Request,
                  db: Session = Depends(database.get_db),
                  admin: models.User = Depends(require_capability("user_admin"))):
    user = db.query(models.User).filter(models.User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    if user.invite_accepted_at or not user.email:
        raise HTTPException(status_code=400, detail="This account has no pending invite to resend")
    invite_url = _issue_invite(user)
    user.updated_at = datetime.utcnow().isoformat()
    db.flush()
    audit_log.record(db, action="user.invite_resend", actor=admin, entity_type="user",
                     entity_id=user.id, entity_label=user.email, request=request)
    db.commit()
    _send_invite_async(user.email, invite_url, ROLE_LABELS.get(user.role, user.role), admin.display_name)
    return {**_user_dict(user), "invite_url": invite_url, "email_status": "sending"}


class CreateUser(BaseModel):
    username: str
    display_name: str = ""
    password: str
    role: str = "bizops"


@router.post("")
def create_user(body: CreateUser, request: Request,
                db: Session = Depends(database.get_db),
                admin: models.User = Depends(require_capability("user_admin"))):
    username = body.username.strip().lower()
    if not username or not body.password:
        raise HTTPException(status_code=400, detail="Username and password are required")
    if len(body.password) < 6:
        raise HTTPException(status_code=400, detail="Password must be at least 6 characters")
    if body.role not in VALID_ROLES:
        raise HTTPException(status_code=400, detail=f"Invalid role: {body.role}")
    if db.query(models.User).filter(models.User.username == username).first():
        raise HTTPException(status_code=409, detail="Username already exists")

    now = datetime.utcnow().isoformat()
    user = models.User(
        username=username,
        display_name=(body.display_name.strip() or username),
        password_hash=hash_password(body.password),
        role=body.role,
        is_active=1,
        created_at=now,
        updated_at=now,
    )
    db.add(user)
    db.flush()
    audit_log.record(db, action="user.create", actor=admin, entity_type="user",
                     entity_id=user.id, entity_label=user.username,
                     details={"role": user.role, "display_name": user.display_name},
                     request=request)
    db.commit()
    db.refresh(user)
    return _user_dict(user)


class UpdateUser(BaseModel):
    display_name: str | None = None
    role: str | None = None
    is_active: bool | None = None
    password: str | None = None


@router.patch("/{user_id}")
def update_user(user_id: int, body: UpdateUser, request: Request,
                db: Session = Depends(database.get_db),
                admin: models.User = Depends(require_capability("user_admin"))):
    user = db.query(models.User).filter(models.User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    if body.role is not None and body.role not in VALID_ROLES:
        raise HTTPException(status_code=400, detail=f"Invalid role: {body.role}")

    demoting = (body.role is not None and body.role != ROLE_ADMIN) or (body.is_active is False)
    # Guardrail 1: never lock yourself out.
    if user.id == admin.id and demoting:
        raise HTTPException(status_code=400, detail="You cannot deactivate or demote your own account")
    # Guardrail 2: never remove the last active admin.
    if demoting and user.role == ROLE_ADMIN:
        others = (db.query(models.User)
                  .filter(models.User.role == ROLE_ADMIN, models.User.is_active == 1,
                          models.User.id != user.id).count())
        if others == 0:
            raise HTTPException(status_code=400, detail="Cannot remove the last active admin")

    changes: dict = {}
    if body.display_name is not None and body.display_name.strip() and body.display_name.strip() != user.display_name:
        changes["display_name"] = {"from": user.display_name, "to": body.display_name.strip()}
        user.display_name = body.display_name.strip()
    if body.role is not None and body.role != user.role:
        changes["role"] = {"from": user.role, "to": body.role}
        user.role = body.role
    if body.is_active is not None and int(body.is_active) != user.is_active:
        changes["is_active"] = {"from": bool(user.is_active), "to": bool(body.is_active)}
        user.is_active = 1 if body.is_active else 0
    pw_changed = False
    if body.password:
        if len(body.password) < 6:
            raise HTTPException(status_code=400, detail="Password must be at least 6 characters")
        user.password_hash = hash_password(body.password)
        pw_changed = True

    if not changes and not pw_changed:
        return _user_dict(user)

    user.updated_at = datetime.utcnow().isoformat()
    action = "user.update"
    if "role" in changes:
        action = "user.role_change"
    elif changes.get("is_active", {}).get("to") is False:
        action = "user.deactivate"
    elif changes.get("is_active", {}).get("to") is True:
        action = "user.reactivate"
    elif pw_changed and not changes:
        action = "user.password_reset"
    details = dict(changes)
    if pw_changed:
        details["password"] = "reset"
    audit_log.record(db, action=action, actor=admin, entity_type="user",
                     entity_id=user.id, entity_label=user.username, details=details,
                     request=request)
    db.commit()
    db.refresh(user)
    return _user_dict(user)
