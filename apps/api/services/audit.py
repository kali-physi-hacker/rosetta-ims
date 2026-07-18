"""Append-only audit trail for catalogue-onboarding decisions.

Every human action on a catalogue item (confirm-match, assign-new-SKU, edit,
reject, supplier-confirm) stages one CatalogueAuditEvent attributed to the
authenticated user and linked to the item and — once it exists — the inventory
Product / SKU. The helper only STAGES the row (db.add); the caller's endpoint
owns the commit so the audit row lands in the same transaction as the change.
"""
import json
from datetime import datetime


def log_event(db, *, action, user, item=None, import_id=None, product_id=None,
              sku_code=None, details=None, request=None):
    """Stage an audit row in the caller's transaction (no commit here).

    Also mirrors the event into the general `audit_log` so the admin Audit panel shows
    catalogue-onboarding actions alongside logins / product / user events."""
    import models
    db.add(models.CatalogueAuditEvent(
        item_id=getattr(item, "id", None),
        import_id=import_id if import_id is not None else getattr(item, "import_id", None),
        product_id=product_id,
        sku_code=sku_code,
        action=action,
        user_id=getattr(user, "id", None),
        username=getattr(user, "username", None),
        display_name=getattr(user, "display_name", None),
        details=json.dumps(details, default=str) if details else None,
        created_at=datetime.utcnow().isoformat(),
    ))
    try:
        from services import audit_log
        audit_log.record(db, action=f"catalogue.{action}", actor=user,
                         entity_type="catalogue_item",
                         entity_id=getattr(item, "id", None),
                         entity_label=(sku_code or getattr(item, "raw_description", None)),
                         details=details, request=request)
    except Exception:
        pass


def diff_changes(before: dict, after: dict) -> dict:
    """{field: {"from": old, "to": new}} for fields that actually changed."""
    changes = {}
    for field, new_val in after.items():
        old_val = before.get(field)
        if old_val != new_val:
            changes[field] = {"from": old_val, "to": new_val}
    return changes
