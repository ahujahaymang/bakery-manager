"""
Unit tests for AuthService PIN set/verify + lockout (app-first pivot, task 3.4).

These exercise the per-user PIN flow against an in-memory registry DB, covering
the happy path and the Req 4 edge cases (invalid PIN not stored, match grants,
mismatch denies + unchanged, 300s lockout after 5 consecutive failures).
"""

from datetime import datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

# Import both model modules so all tables register on the shared Base.
import app.models  # noqa: F401
from app.database import Base
from app.models import Tenant
from app.auth.models_auth import User
from app.auth import security
from app.auth.auth_service import (
    AuthService,
    DeliveryResult,
    InvalidPinError,
    PinLockedError,
    UnknownUserError,
    _MAX_PIN_ATTEMPTS,
    _PIN_LOCKOUT,
)


PHONE = "+919876543210"
GOOD_PIN = "1357"


class StubOtpSender:
    """Delivery stub — PIN tests never send OTPs, but AuthService requires one."""

    async def send(self, phone, code):
        return DeliveryResult(success=True, channel="telegram")


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
def user(registry_db):
    tenant = Tenant(chat_id="chat_owner")
    registry_db.add(tenant)
    registry_db.commit()
    u = User(tenant_id=tenant.tenant_id, name="Owner", phone=PHONE, role="owner")
    registry_db.add(u)
    registry_db.commit()
    registry_db.refresh(u)
    return u


@pytest.fixture()
def svc(registry_db):
    return AuthService(registry_db, StubOtpSender())


# ── set_pin (Req 4.1, 4.2) ────────────────────────────────────────────────────

def test_set_pin_stores_argon2_hash(svc, registry_db, user):
    svc.set_pin(user.user_id, GOOD_PIN)
    registry_db.refresh(user)
    # Stored as an argon2 hash, never plaintext.
    assert user.pin_hash and user.pin_hash != GOOD_PIN
    assert user.pin_hash.startswith("$argon2")
    assert security.verify_pin(user.pin_hash, GOOD_PIN)


@pytest.mark.parametrize("pin", ["1234", "12345678"])  # 4 and 8 digit bounds
def test_set_pin_accepts_boundary_lengths(svc, registry_db, user, pin):
    svc.set_pin(user.user_id, pin)
    registry_db.refresh(user)
    assert security.verify_pin(user.pin_hash, pin)


@pytest.mark.parametrize(
    "bad_pin",
    [
        "123",          # too short
        "123456789",    # too long
        "12a4",         # non-numeric
        "12 4",         # whitespace
        "",             # empty
        "٤٥٦٧",         # unicode digit look-alikes (ASCII-only rule)
    ],
)
def test_set_pin_invalid_rejected_and_stores_nothing(svc, registry_db, user, bad_pin):
    with pytest.raises(InvalidPinError):
        svc.set_pin(user.user_id, bad_pin)
    registry_db.refresh(user)
    assert user.pin_hash is None  # Req 4.2: nothing stored


def test_set_pin_does_not_overwrite_on_invalid(svc, registry_db, user):
    svc.set_pin(user.user_id, GOOD_PIN)
    registry_db.refresh(user)
    original = user.pin_hash
    with pytest.raises(InvalidPinError):
        svc.set_pin(user.user_id, "12")
    registry_db.refresh(user)
    assert user.pin_hash == original  # Req 4.2: unchanged


def test_set_pin_unknown_user_raises(svc):
    with pytest.raises(UnknownUserError):
        svc.set_pin(uuid4(), GOOD_PIN)


def test_set_pin_resets_prior_failure_state(svc, registry_db, user):
    svc.set_pin(user.user_id, GOOD_PIN)
    # Rack up some failures, then reset by setting a new PIN.
    svc.verify_pin(user.user_id, "0000")
    registry_db.refresh(user)
    assert user.pin_failed_count == 1
    svc.set_pin(user.user_id, "2468")
    registry_db.refresh(user)
    assert user.pin_failed_count == 0
    assert user.pin_locked_until is None


# ── verify_pin match / mismatch (Req 4.3, 4.4) ────────────────────────────────

def test_verify_pin_match_grants_and_resets_counter(svc, registry_db, user):
    svc.set_pin(user.user_id, GOOD_PIN)
    svc.verify_pin(user.user_id, "0000")  # one failure first
    assert svc.verify_pin(user.user_id, GOOD_PIN) is True
    registry_db.refresh(user)
    assert user.pin_failed_count == 0  # Req 4.3: reset on success
    assert user.pin_locked_until is None


def test_verify_pin_mismatch_denies_and_leaves_hash_unchanged(svc, registry_db, user):
    svc.set_pin(user.user_id, GOOD_PIN)
    registry_db.refresh(user)
    stored = user.pin_hash
    assert svc.verify_pin(user.user_id, "9999") is False  # Req 4.4
    registry_db.refresh(user)
    assert user.pin_hash == stored  # unchanged
    assert user.pin_failed_count == 1


def test_verify_pin_no_pin_set_returns_false(svc, user):
    # No PIN set — can never match.
    assert svc.verify_pin(user.user_id, GOOD_PIN) is False


def test_verify_pin_unknown_user_raises(svc):
    with pytest.raises(UnknownUserError):
        svc.verify_pin(uuid4(), GOOD_PIN)


# ── verify_pin lockout (Req 4.5) ──────────────────────────────────────────────

def test_verify_pin_locks_after_five_consecutive_failures(svc, registry_db, user):
    svc.set_pin(user.user_id, GOOD_PIN)
    # 5 consecutive wrong PINs arm the lockout.
    for _ in range(_MAX_PIN_ATTEMPTS):
        assert svc.verify_pin(user.user_id, "0000") is False
    registry_db.refresh(user)
    assert user.pin_locked_until is not None
    # Now even the correct PIN is refused without being checked.
    with pytest.raises(PinLockedError) as exc:
        svc.verify_pin(user.user_id, GOOD_PIN)
    assert 0 < exc.value.retry_after <= int(_PIN_LOCKOUT.total_seconds()) + 1


def test_verify_pin_correct_before_threshold_resets_streak(svc, registry_db, user):
    svc.set_pin(user.user_id, GOOD_PIN)
    # 4 failures (one short of the threshold), then a success resets the streak.
    for _ in range(_MAX_PIN_ATTEMPTS - 1):
        svc.verify_pin(user.user_id, "0000")
    assert svc.verify_pin(user.user_id, GOOD_PIN) is True
    # A subsequent single failure must not immediately lock (streak was reset).
    assert svc.verify_pin(user.user_id, "0000") is False
    registry_db.refresh(user)
    assert user.pin_locked_until is None
    assert user.pin_failed_count == 1


def test_verify_pin_lockout_clears_after_window_elapses(svc, registry_db, user):
    svc.set_pin(user.user_id, GOOD_PIN)
    for _ in range(_MAX_PIN_ATTEMPTS):
        svc.verify_pin(user.user_id, "0000")
    # Simulate the 300s window having already elapsed.
    registry_db.refresh(user)
    user.pin_locked_until = datetime.utcnow() - timedelta(seconds=1)
    registry_db.commit()
    # Lockout cleared — the correct PIN is accepted again.
    assert svc.verify_pin(user.user_id, GOOD_PIN) is True
    registry_db.refresh(user)
    assert user.pin_locked_until is None
    assert user.pin_failed_count == 0
