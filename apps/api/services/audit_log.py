"""General audit-log recorder. One call per auditable event; rows are append-only.

Use `record(...)` to stage a row in the caller's transaction (commit=True for standalone
events like logins). `diff(before, after)` produces a compact {field: {from, to}} change set.
"""
import json
from datetime import datetime


def client_ip(request) -> str | None:
    if request is None:
        return None
    # Behind Caddy the real client is in X-Forwarded-For (first hop).
    xff = request.headers.get("x-forwarded-for")
    if xff:
        return xff.split(",")[0].strip()
    return request.client.host if request.client else None


def _ua(request) -> str | None:
    return request.headers.get("user-agent") if request is not None else None


def record(db, *, action, actor=None, entity_type=None, entity_id=None, entity_label=None,
           details=None, request=None, ip=None, user_agent=None, commit=False):
    """Append an audit row. Actor identity is snapshotted so it survives rename/deactivation.

    `actor` is a User (or None for anonymous events like a failed login). `details` is any
    JSON-serialisable dict (e.g. a before/after diff). IP + user-agent are pulled from
    `request` unless passed explicitly. Stages in the caller's transaction unless commit=True.
    """
    import models
    row = models.AuditLog(
        created_at=datetime.utcnow().isoformat(),
        action=action,
        actor_user_id=getattr(actor, "id", None),
        actor_username=getattr(actor, "username", None),
        actor_display_name=getattr(actor, "display_name", None),
        actor_role=getattr(actor, "role", None),
        entity_type=entity_type,
        entity_id=str(entity_id) if entity_id is not None else None,
        entity_label=entity_label,
        details=json.dumps(details, default=str) if details else None,
        ip=ip if ip is not None else client_ip(request),
        user_agent=user_agent if user_agent is not None else _ua(request),
    )
    db.add(row)
    if commit:
        db.commit()
    return row


def diff(before: dict, after: dict) -> dict:
    """{field: {"from": old, "to": new}} for every key in `after` whose value changed."""
    out = {}
    for key, new in after.items():
        old = before.get(key)
        if old != new:
            out[key] = {"from": old, "to": new}
    return out
