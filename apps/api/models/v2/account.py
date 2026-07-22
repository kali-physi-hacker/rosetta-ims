"""Account and authentication related models."""
from .base import Base, Column, Integer, String, ForeignKey


class User(Base):
    __tablename__ = "users"

    id            = Column(Integer, primary_key=True, autoincrement=True)
    username      = Column(String, unique=True, nullable=False)
    display_name  = Column(String, nullable=False)
    password_hash = Column(String, nullable=False)
    role          = Column(String, nullable=False, default='bizops')  # 'admin' | 'bizops' | 'data_entry'
    is_active     = Column(Integer, nullable=False, default=1)
    created_at    = Column(String, nullable=False)
    updated_at    = Column(String, nullable=True)    # last time the account was changed by an admin
    last_login_at = Column(String, nullable=True)    # stamped on each successful login
    email         = Column(String, nullable=True)    # contact email (set on invite / during onboarding)
    # Invite-by-email onboarding: a pending invite has a token + expiry and is_active=0 until accepted.
    invite_token       = Column(String, nullable=True, index=True)
    invite_expires_at  = Column(String, nullable=True)
    invite_accepted_at = Column(String, nullable=True)
    invited_by         = Column(String, nullable=True)   # display_name of the admin who invited


class AccessAcknowledgement(Base):
    """Records a tech-team auditor's click-wrap NDA acceptance + access request.
    On submit, the system also emails chris@algogroup.io with the requestor cc'd."""
    __tablename__ = "access_acknowledgements"

    id               = Column(Integer, primary_key=True, autoincrement=True)
    user_id          = Column(Integer, ForeignKey("users.id"), nullable=False)
    github_username  = Column(String, nullable=False)
    full_name_typed  = Column(String, nullable=False)
    email_requestor  = Column(String, nullable=True)   # email to CC on the request notification
    terms_version    = Column(String, nullable=False, default='v1-2026-06')
    ip_address       = Column(String, nullable=True)
    accepted_at      = Column(String, nullable=False)
    email_sent_at    = Column(String, nullable=True)   # set if notification email succeeded
    email_send_error = Column(String, nullable=True)   # captured error if send failed