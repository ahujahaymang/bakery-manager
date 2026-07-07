"""
Tests for RegistryChannelResolver phone → Notification_Channel resolution.

Focus: a registered owner whose ``User.phone`` is stored with a leading ``+``
(as AuthService/onboarding store it) must still resolve to their Telegram
channel, so the app-first OTP is delivered over Telegram instead of silently
falling back to SMS (Req 3.1). Uses an in-memory registry SQLite with a
StaticPool, mirroring tests/test_onboarding.py.
"""

from uuid import uuid4

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401  (register business tables)
import app.auth.models_auth  # noqa: F401  (register auth tables)
from app.database import Base
from app.models import Tenant
from app.auth.models_auth import User
from app.auth.otp_sender import RegistryChannelResolver, DeliveryChannel


@pytest.fixture()
def registry():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(engine, "connect")
    def _fk(conn, _):
        conn.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine)
    yield SessionLocal
    Base.metadata.drop_all(engine)
    engine.dispose()


def _seed_owner(SessionLocal, *, phone, chat_id="555001", platform="telegram"):
    db = SessionLocal()
    try:
        tenant = Tenant(chat_id=chat_id, business_name="Priya's Kitchen", messaging_platform=platform)
        db.add(tenant)
        db.commit()
        db.refresh(tenant)
        db.add(User(tenant_id=tenant.tenant_id, name="Priya", phone=phone, role="owner"))
        db.commit()
        return tenant.chat_id
    finally:
        db.close()


def _resolver(SessionLocal):
    return RegistryChannelResolver(lambda: SessionLocal())


def test_stored_plus_phone_resolves_to_telegram_channel(registry):
    chat_id = _seed_owner(registry, phone="+919876543210", chat_id="777042")
    target = _resolver(registry).resolve("+919876543210")
    assert target is not None
    assert target.platform == "telegram"
    assert target.destination == chat_id


def test_digits_only_input_still_matches_stored_plus_phone(registry):
    _seed_owner(registry, phone="+919876543210", chat_id="777043")
    # Even if the caller passes bare digits, it resolves the stored +form.
    target = _resolver(registry).resolve("919876543210")
    assert target is not None
    assert target.platform == "telegram"


def test_phone_without_plus_resolves(registry):
    # Demo-owner style number stored without a country-code/plus.
    _seed_owner(registry, phone="9873416371", chat_id="777044")
    target = _resolver(registry).resolve("9873416371")
    assert target is not None


def test_unknown_phone_returns_none(registry):
    _seed_owner(registry, phone="+919876543210")
    assert _resolver(registry).resolve("+910000000000") is None


def test_channel_sender_reports_usable_channel_when_telegram_send_wired(registry):
    """With a Telegram send path wired, a resolved phone has a usable channel."""
    from app.auth.otp_sender import NotificationChannelSender

    _seed_owner(registry, phone="+919876543210")

    async def _send(dest, msg):
        return None

    sender = NotificationChannelSender(_resolver(registry), telegram_send=_send)
    assert sender.channel_for("+919876543210") == DeliveryChannel.TELEGRAM
    assert sender.has_channel("+919876543210") is True


def test_channel_sender_no_send_path_means_no_channel(registry):
    """Without a wired send path, the channel is unusable → caller uses SMS."""
    from app.auth.otp_sender import NotificationChannelSender

    _seed_owner(registry, phone="+919876543210")
    sender = NotificationChannelSender(_resolver(registry))  # no telegram_send
    assert sender.channel_for("+919876543210") is None
