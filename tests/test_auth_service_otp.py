"""
Unit tests for AuthService OTP request/verify (app-first pivot, task 3.2).

These exercise the phone + OTP → device session flow against an in-memory
registry DB with a stub OtpSender, covering the happy path and the Req 2
edge cases (invalid phone, expiry, wrong code, attempt cap, single use,
token validity clamp).
"""

import asyncio
from datetime import datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

# Import both model modules so all tables register on the shared Base.
import app.models  # noqa: F401
from app.database import Base
from app.models import Tenant
from app.auth.models_auth import User, Device, OtpChallenge
from app.auth import security
from app.auth.auth_service import (
    AuthService,
    DeliveryResult,
    DeviceSession,
    OtpRequestResult,
    InvalidPhoneError,
    UnknownPhoneError,
    OtpDeliveryError,
    OtpExpiredError,
    OtpInvalidError,
    OtpMaxAttemptsError,
    OtpNotFoundError,
    _MAX_OTP_ATTEMPTS,
    _OTP_DIGITS,
)


PHONE = "+919876543210"


class StubOtpSender:
    """Records sent codes and reports a configurable delivery outcome."""

    def __init__(self, success=True, channel="telegram"):
        self._success = success
        self._channel = channel
        self.sent = []  # list of (phone, code)

    async def send(self, phone, code):
        self.sent.append((phone, code))
        return DeliveryResult(success=self._success, channel=self._channel)


@pytest.fixture()
def registry_db():
    engine = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}
    )

    @event.listens_for(engine, "connect")
    def _fk(conn, _):
        conn.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()
    Base.metadata.drop_all(engine)
    engine.dispose()


@pytest.fixture()
def owner(registry_db):
    tenant = Tenant(chat_id="chat_owner")
    registry_db.add(tenant)
    registry_db.commit()
    user = User(tenant_id=tenant.tenant_id, name="Owner", phone=PHONE, role="owner")
    registry_db.add(user)
    registry_db.commit()
    registry_db.refresh(user)
    return user


def _request(svc, phone=PHONE):
    return asyncio.run(svc.request_otp(phone))


# ── request_otp ───────────────────────────────────────────────────────────────

def test_request_otp_creates_hashed_challenge_and_delivers(registry_db, owner):
    sender = StubOtpSender(channel="telegram")
    svc = AuthService(registry_db, sender)

    result = _request(svc)

    assert isinstance(result, OtpRequestResult)
    assert result.delivery_channel == "telegram"
    # A single challenge exists, code is hashed (not stored plaintext), 5-min expiry.
    challenge = registry_db.query(OtpChallenge).one()
    assert challenge.code_hash and "$" in challenge.code_hash
    assert len(sender.sent) == 1
    _, sent_code = sender.sent[0]
    assert len(sent_code) == _OTP_DIGITS and sent_code.isdigit()
    assert challenge.code_hash != sent_code
    delta = challenge.expires_at - challenge.created_at
    assert timedelta(minutes=4) <= delta <= timedelta(minutes=6)


def test_request_otp_rejects_invalid_phone_without_challenge(registry_db, owner):
    svc = AuthService(registry_db, StubOtpSender())
    with pytest.raises(InvalidPhoneError):
        asyncio.run(svc.request_otp("abc123"))
    assert registry_db.query(OtpChallenge).count() == 0  # Req 2.3


def test_request_otp_unknown_phone_rejected(registry_db, owner):
    svc = AuthService(registry_db, StubOtpSender())
    with pytest.raises(UnknownPhoneError):
        asyncio.run(svc.request_otp("+911111111111"))


def test_request_otp_delivery_failure_does_not_record(registry_db, owner):
    svc = AuthService(registry_db, StubOtpSender(success=False))
    with pytest.raises(OtpDeliveryError):
        _request(svc)
    # Req 3.4: not recorded as delivered — challenge removed.
    assert registry_db.query(OtpChallenge).count() == 0


def test_request_otp_supersedes_previous_challenge(registry_db, owner):
    svc = AuthService(registry_db, StubOtpSender())
    _request(svc)
    _request(svc)
    active = registry_db.query(OtpChallenge).filter(OtpChallenge.consumed_at.is_(None)).all()
    assert len(active) == 1  # only the newest OTP stays live


# ── verify_otp ────────────────────────────────────────────────────────────────

def _seed_challenge(registry_db, code, *, expires_in=timedelta(minutes=5), attempts=0, phone=PHONE):
    now = datetime.utcnow()
    challenge = OtpChallenge(
        phone=phone,
        code_hash=security.hash_otp(code),
        expires_at=now + expires_in,
        attempt_count=attempts,
        created_at=now,
    )
    registry_db.add(challenge)
    registry_db.commit()
    return challenge


def test_verify_otp_success_issues_clamped_device_session(registry_db, owner):
    svc = AuthService(registry_db, StubOtpSender())
    _seed_challenge(registry_db, "123456")

    session = svc.verify_otp(PHONE, "123456", {"label": "Pixel"})

    assert isinstance(session, DeviceSession)
    assert session.user_id == owner.user_id
    assert session.tenant_id == owner.tenant_id
    # Req 2.6: validity clamped to 30–90 days (allow a small clock-drift margin
    # for the time elapsed between issuance and this assertion).
    validity = session.expires_at - datetime.utcnow()
    assert timedelta(days=30) - timedelta(seconds=5) <= validity <= timedelta(days=90)
    # Raw token returned once; only its hash is stored.
    device = registry_db.query(Device).one()
    assert device.token_hash == security.hash_token_for_storage(session.token)
    assert device.label == "Pixel"
    # Req 2.5: challenge consumed (single use).
    challenge = registry_db.query(OtpChallenge).one()
    assert challenge.consumed_at is not None


def test_verify_otp_single_use_rejects_replay(registry_db, owner):
    svc = AuthService(registry_db, StubOtpSender())
    _seed_challenge(registry_db, "123456")
    svc.verify_otp(PHONE, "123456")
    with pytest.raises(OtpNotFoundError):
        svc.verify_otp(PHONE, "123456")


def test_verify_otp_wrong_code_rejected_and_counts(registry_db, owner):
    svc = AuthService(registry_db, StubOtpSender())
    _seed_challenge(registry_db, "123456")
    with pytest.raises(OtpInvalidError):
        svc.verify_otp(PHONE, "000000")
    assert registry_db.query(OtpChallenge).one().attempt_count == 1
    assert registry_db.query(Device).count() == 0  # no session on failure


def test_verify_otp_expired_rejected(registry_db, owner):
    svc = AuthService(registry_db, StubOtpSender())
    _seed_challenge(registry_db, "123456", expires_in=timedelta(minutes=-1))
    with pytest.raises(OtpExpiredError):
        svc.verify_otp(PHONE, "123456")


def test_verify_otp_invalidates_after_five_attempts(registry_db, owner):
    svc = AuthService(registry_db, StubOtpSender())
    _seed_challenge(registry_db, "123456")
    # Attempts 1..4 report an ordinary invalid-code failure.
    for _ in range(_MAX_OTP_ATTEMPTS - 1):
        with pytest.raises(OtpInvalidError):
            svc.verify_otp(PHONE, "000000")
    # The 5th wrong attempt invalidates the OTP.
    with pytest.raises(OtpMaxAttemptsError):
        svc.verify_otp(PHONE, "000000")
    # Even the correct code no longer works — a new OTP is required.
    with pytest.raises((OtpMaxAttemptsError, OtpNotFoundError)):
        svc.verify_otp(PHONE, "123456")


def test_verify_otp_no_active_challenge(registry_db, owner):
    svc = AuthService(registry_db, StubOtpSender())
    with pytest.raises(OtpNotFoundError):
        svc.verify_otp(PHONE, "123456")
