"""JWT auth utilities — user extraction and role enforcement."""
import os
from datetime import datetime, timedelta, timezone
from typing import Optional

from jose import JWTError, jwt
from passlib.context import CryptContext
from fastapi import Depends, HTTPException
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.orm import Session

import database
import models

SECRET_KEY = os.environ.get("JWT_SECRET", "dev-secret-key-change-in-production")
ALGORITHM = "HS256"
TOKEN_EXPIRE_DAYS = 30

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
_bearer = HTTPBearer(auto_error=False)


def verify_password(plain: str, hashed: str) -> bool:
    return pwd_context.verify(plain, hashed)


def hash_password(plain: str) -> str:
    return pwd_context.hash(plain)


def create_access_token(user: "models.User") -> str:
    expire = datetime.now(timezone.utc) + timedelta(days=TOKEN_EXPIRE_DAYS)
    return jwt.encode(
        {"sub": str(user.id), "username": user.username,
         "display_name": user.display_name, "role": user.role, "exp": expire},
        SECRET_KEY, algorithm=ALGORITHM,
    )


def _decode(token: str) -> dict | None:
    try:
        return jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
    except JWTError:
        return None


def get_current_user(
    creds: Optional[HTTPAuthorizationCredentials] = Depends(_bearer),
    db: Session = Depends(database.get_db),
) -> Optional["models.User"]:
    """Returns the logged-in user, or None if no valid JWT is present."""
    if not creds:
        return None
    payload = _decode(creds.credentials)
    if not payload:
        return None
    user = db.query(models.User).filter(models.User.id == int(payload["sub"])).first()
    return user if (user and user.is_active) else None


def require_user(
    creds: Optional[HTTPAuthorizationCredentials] = Depends(_bearer),
    db: Session = Depends(database.get_db),
) -> "models.User":
    """Like get_current_user but raises 401 if unauthenticated."""
    user = get_current_user(creds, db)
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required")
    return user


def require_admin(user: "models.User" = Depends(require_user)) -> "models.User":
    if user.role != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")
    return user
