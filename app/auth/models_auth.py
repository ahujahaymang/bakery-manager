"""
Authentication models — live in the registry DB.

Login must resolve ``phone → tenant → user`` *before* any tenant business DB
is opened. In the per-tenant-file model, we cannot know which tenant file to
open until we know the user. Therefore ``User``, ``Device``, ``OtpChallenge``,
and ``WebAuthnCredential`` live in the registry DB (which already holds the
authoritative ``Tenant`` rows and the ``chat_id → tenant`` mapping).

All models are registered on the same ``Base`` as the business models, so they
are created by ``Base.metadata.create_all`` on the registry engine. Reuse the
``PortableUUID`` type decorator from ``app.models`` for UUID primary/foreign
keys so columns stay portable between SQLite and PostgreSQL.
"""

from sqlalchemy import Column, String, Integer, DateTime, ForeignKey, UniqueConstraint, Text
import uuid
from datetime import datetime

from app.database import Base
from app.models import PortableUUID


class User(Base):
    """
    An authenticated person within a tenant.

    Resolved by (tenant_id, phone) during login. ``role`` distinguishes the
    business owner from staff members. ``pin_hash`` is NULL until the user
    sets a PIN; ``pin_failed_count`` and ``pin_locked_until`` implement the
    PIN lockout window.
    """
    __tablename__ = "users"

    user_id = Column(PortableUUID(), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(PortableUUID(), ForeignKey("tenants.tenant_id"), nullable=False, index=True)
    name = Column(String, nullable=False)
    phone = Column(String, nullable=False, index=True)          # E.164-normalized
    role = Column(String, nullable=False)                       # "owner" | "staff"
    pin_hash = Column(String, nullable=True)                    # argon2; NULL until set
    pin_failed_count = Column(Integer, nullable=False, default=0)
    pin_locked_until = Column(DateTime, nullable=True)          # lockout window
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    __table_args__ = (
        UniqueConstraint("tenant_id", "phone", name="uq_user_tenant_phone"),
    )


class Device(Base):
    """
    A remembered device session for a user.

    ``token_hash`` is the SHA-256 of the device session token (the raw token is
    never stored). ``expires_at`` bounds the remember-me window and
    ``revoked_at`` marks a device as logged out / revoked.
    """
    __tablename__ = "devices"

    device_id = Column(PortableUUID(), primary_key=True, default=uuid.uuid4)
    user_id = Column(PortableUUID(), ForeignKey("users.user_id"), nullable=False, index=True)
    tenant_id = Column(PortableUUID(), ForeignKey("tenants.tenant_id"), nullable=False, index=True)
    token_hash = Column(String, nullable=False, index=True)     # sha256 of device session token
    label = Column(String, nullable=True)                       # user-agent / device name
    issued_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    expires_at = Column(DateTime, nullable=False)               # 30–90 days
    revoked_at = Column(DateTime, nullable=True)


class OtpChallenge(Base):
    """
    A one-time passcode challenge issued to a phone number.

    ``code_hash`` stores the hash of the 4–8 digit OTP. A challenge is
    single-use (``consumed_at``), time-boxed (``expires_at``), and invalidated
    after too many attempts (``attempt_count``). ``delivery_channel`` records
    how the code was sent.
    """
    __tablename__ = "otp_challenges"

    challenge_id = Column(PortableUUID(), primary_key=True, default=uuid.uuid4)
    phone = Column(String, nullable=False, index=True)
    code_hash = Column(String, nullable=False)                  # hash of 4–8 digit OTP
    expires_at = Column(DateTime, nullable=False)               # issued_at + 5 min
    attempt_count = Column(Integer, nullable=False, default=0)  # invalidate at 5
    consumed_at = Column(DateTime, nullable=True)               # single-use
    delivery_channel = Column(String, nullable=True)            # "telegram" | "whatsapp" | "sms"
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)


class WebAuthnCredential(Base):
    """
    A WebAuthn (passkey) credential registered to a user.

    ``credential_id`` is the base64url-encoded credential id issued by the
    authenticator and serves as the primary key. ``sign_count`` is used for
    clone detection during assertion verification.
    """
    __tablename__ = "webauthn_credentials"

    credential_id = Column(String, primary_key=True)            # base64url credential id
    user_id = Column(PortableUUID(), ForeignKey("users.user_id"), nullable=False, index=True)
    public_key = Column(Text, nullable=False)
    sign_count = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
