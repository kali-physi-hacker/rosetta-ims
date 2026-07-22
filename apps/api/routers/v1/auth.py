"""Authentication endpoints — login, me, logout, change-password. All audited."""
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session

import database
import models
from dependencies import verify_password, hash_password, create_access_token, get_current_user, require_user
from permissions import ROLE_LABELS
from services import audit_log

router = APIRouter(prefix="/auth", tags=["auth"])


class LoginRequest(BaseModel):
    username: str
    password: str


def _user_dict(user: models.User) -> dict:
    return {
        "id":           user.id,
        "username":     user.username,
        "display_name": user.display_name,
        "role":         user.role,
        "role_label":   ROLE_LABELS.get(user.role, user.role),
    }


@router.post("/login")
def login(body: LoginRequest, request: Request, db: Session = Depends(database.get_db)):
    username = body.username.strip().lower()
    user = db.query(models.User).filter(models.User.username == username).first()
    if not user or not user.is_active or not verify_password(body.password, user.password_hash):
        reason = ("unknown_user" if not user
                  else "inactive" if not user.is_active else "bad_password")
        audit_log.record(db, action="login.fail", actor=user, entity_type="auth",
                         entity_label=username, details={"username": username, "reason": reason},
                         request=request, commit=True)
        raise HTTPException(status_code=401, detail="Invalid username or password")

    user.last_login_at = datetime.utcnow().isoformat()
    audit_log.record(db, action="login.success", actor=user, entity_type="auth",
                     entity_label=user.username, request=request)
    db.commit()
    return {"token": create_access_token(user), "user": _user_dict(user)}


@router.post("/logout")
def logout(request: Request, db: Session = Depends(database.get_db),
           user: models.User = Depends(require_user)):
    audit_log.record(db, action="logout", actor=user, entity_type="auth",
                     entity_label=user.username, request=request, commit=True)
    return {"ok": True}


@router.get("/me")
def get_me(current_user: models.User | None = Depends(get_current_user)):
    if not current_user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return _user_dict(current_user)


class ChangePassword(BaseModel):
    current_password: str
    new_password: str


@router.post("/change-password")
def change_password(body: ChangePassword, request: Request,
                    db: Session = Depends(database.get_db),
                    user: models.User = Depends(require_user)):
    """Any signed-in user can change their OWN password."""
    if not verify_password(body.current_password, user.password_hash):
        raise HTTPException(status_code=403, detail="Current password is incorrect")
    if len(body.new_password) < 6:
        raise HTTPException(status_code=400, detail="New password must be at least 6 characters")
    user.password_hash = hash_password(body.new_password)
    user.updated_at = datetime.utcnow().isoformat()
    audit_log.record(db, action="user.password_change", actor=user, entity_type="user",
                     entity_id=user.id, entity_label=user.username,
                     details={"self": True}, request=request)
    db.commit()
    return {"ok": True}


# ── Invite onboarding (public — the invited user has no session yet) ──────────────

def _invite_or_404(db: Session, token: str) -> models.User:
    user = db.query(models.User).filter(models.User.invite_token == token).first()
    if not user or user.invite_accepted_at:
        raise HTTPException(status_code=404, detail="This invite link is invalid or has already been used")
    return user


@router.get("/invite/{token}")
def get_invite(token: str, db: Session = Depends(database.get_db)):
    """Validate an invite token and return what to prefill on the onboarding page."""
    user = _invite_or_404(db, token)
    expired = bool(user.invite_expires_at and user.invite_expires_at < datetime.utcnow().isoformat())
    suggested = "" if (user.display_name or "").startswith("pending-") else user.display_name
    return {
        "valid":      not expired,
        "expired":    expired,
        "email":      user.email,
        "display_name": suggested or (user.email.split("@")[0] if user.email else ""),
        "role":       user.role,
        "role_label": ROLE_LABELS.get(user.role, user.role),
        "invited_by": user.invited_by,
    }


class AcceptInvite(BaseModel):
    token: str
    username: str
    display_name: str
    email: str
    password: str


@router.post("/accept-invite")
def accept_invite(body: AcceptInvite, request: Request, db: Session = Depends(database.get_db)):
    """Complete onboarding from an invite: set username/name/email/password, activate, log in."""
    user = _invite_or_404(db, body.token)
    if user.invite_expires_at and user.invite_expires_at < datetime.utcnow().isoformat():
        raise HTTPException(status_code=400, detail="This invite link has expired — ask an admin to resend it")

    username = body.username.strip().lower()
    email = body.email.strip().lower()
    if not username:
        raise HTTPException(status_code=400, detail="Username is required")
    if not email or "@" not in email:
        raise HTTPException(status_code=400, detail="A valid email is required")
    if len(body.password) < 6:
        raise HTTPException(status_code=400, detail="Password must be at least 6 characters")
    clash = (db.query(models.User)
             .filter(models.User.username == username, models.User.id != user.id).first())
    if clash:
        raise HTTPException(status_code=409, detail="That username is already taken")

    now = datetime.utcnow().isoformat()
    user.username = username
    user.email = email
    user.display_name = body.display_name.strip() or username
    user.password_hash = hash_password(body.password)
    user.is_active = 1
    user.invite_accepted_at = now
    user.invite_token = None
    user.last_login_at = now
    user.updated_at = now
    audit_log.record(db, action="user.accept_invite", actor=user, entity_type="user",
                     entity_id=user.id, entity_label=user.username, request=request)
    db.commit()
    return {"token": create_access_token(user), "user": _user_dict(user)}
