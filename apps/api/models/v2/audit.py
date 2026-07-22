"""Audit and logging related models."""
from .base import Base, Column, Integer, String, ForeignKey


class AuditLog(Base):
    """General append-only audit trail: logins, user-management, and who-edited-what across
    the system (products, catalogue, reference data, sheet sync). One row per event, never
    updated. Actor identity is snapshotted so it survives user rename/deactivation."""
    __tablename__ = "audit_log"

    id                 = Column(Integer, primary_key=True, autoincrement=True)
    created_at         = Column(String, nullable=False, index=True)
    action             = Column(String, nullable=False, index=True)   # e.g. login.success, product.update, user.role_change
    actor_user_id      = Column(Integer, nullable=True, index=True)
    actor_username     = Column(String, nullable=True)
    actor_display_name = Column(String, nullable=True)
    actor_role         = Column(String, nullable=True)
    entity_type        = Column(String, nullable=True, index=True)    # product | user | catalogue_item | category | collection | sheet | auth
    entity_id          = Column(String, nullable=True, index=True)
    entity_label       = Column(String, nullable=True)                # human label (sku / username / name)
    details            = Column(String, nullable=True)                # JSON: before/after diff, reason, etc.
    ip                 = Column(String, nullable=True)
    user_agent         = Column(String, nullable=True)