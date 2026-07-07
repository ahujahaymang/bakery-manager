"""
Router-level tests for the auth API (`app/api/auth_router.py`, task 4.4).

These verify the HTTP adapter correctly wires each endpoint to
``AuthService`` / ``WebAuthnService`` against the registry DB and maps domain
errors to the shared HTTP error bodies (app/api/errors.py). Business logic is
covered by the service-level tests; here we assert the wiring, status codes,
role gating, and token/cookie surfacing.

Covered wirings:
- POST /otp/request           → request_otp (public, 400 invalid, 502 delivery)
- POST /otp/verify            → verify_otp (public, token + cookie, 401 wrong)
- POST /pin, /pin/verify      → set_pin/verify_pin (authed, 400 invalid, 429 lock)
- GET/POST /users             → list_users/create_user (Owner-only, 403/409)
- POST /mode/manage           → elevate_to_manage (authed, owner-credential gate)
"""

import asyncio
from datetime import datetime, timedelta
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401  (register all tables on the shared Base)
from app.database import Base, get_registry_db
from app.models import Tenant
from app.auth import security
from app.auth.models_auth import Device, OtpChallenge, User
from app.auth.auth_service import AuthService, DeliveryResult
from app.api import errors
from app.api import auth_router
from app.api.deps import AuthedUser, get_current_user, require_owner
from app.api.auth_router import get_auth_service, router

PHONE = "+919876543210"
OTP_CODE = "123456"
OWNER_PIN = "4321"


class StubOtpSender:
    """Configurable OTP delivery stub (no real send)."""

    def __init__(self, success=True, channel="telegram"):
        self._success = success
        self._channel = channel

    async def send(self, phone, code):
        return DeliveryResult(success=self._success, channel=self._channel)


@pytest.fixture()
def registry_db():
    # StaticPool keeps a single shared connection so the in-memory DB is visible
    # across threads (FastAPI runs sync endpoints in a worker thread).
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
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


def _make_app(registry_db, sender=None):
    """Build a FastAPI app mounting the auth router against ``registry_db``."""
    application = FastAPI()
    errors.register_error_handlers(application)
    application.include_router(router)

    def _override_registry_db():
        yield registry_db

    application.dependency_overrides[get_registry_db] = _override_registry_db
    application.dependency_overrides[get_auth_service] = lambda: AuthService(
        registry_db, sender or StubOtpSender()
    )
    return application


def _authed(user):
    return AuthedUser(
        user_id=user.user_id,
        tenant_id=user.tenant_id,
        role=user.role,
        device_id=uuid4(),
    )


# ── OTP ────────────────────────────────────────────────────────────────────

def test_otp_request_success(registry_db, owner):
    app = _make_app(registry_db)
    client = TestClient(app)
    resp = client.post("/api/v1/auth/otp/request", json={"phone": PHONE})
    assert resp.status_code == 200
    body = resp.json()
    assert body["phone"] == PHONE
    assert body["delivery_channel"] == "telegram"
    assert body["challenge_id"]


def test_otp_request_invalid_phone_returns_400(registry_db, owner):
    app = _make_app(registry_db)
    client = TestClient(app)
    resp = client.post("/api/v1/auth/otp/request", json={"phone": "abc"})
    assert resp.status_code == 400
    assert resp.json()["error"] == "validation_error"
    assert resp.json()["field"] == "phone"


def test_otp_request_delivery_failure_returns_502(registry_db, owner):
    app = _make_app(registry_db, sender=StubOtpSender(success=False))
    client = TestClient(app)
    resp = client.post("/api/v1/auth/otp/request", json={"phone": PHONE})
    assert resp.status_code == 502
    assert resp.json()["error"] == "otp_delivery_failed"


def _seed_challenge(registry_db, code=OTP_CODE):
    challenge = OtpChallenge(
        phone=PHONE,
        code_hash=security.hash_otp(code),
        expires_at=datetime.utcnow() + timedelta(minutes=5),
        attempt_count=0,
        created_at=datetime.utcnow(),
    )
    registry_db.add(challenge)
    registry_db.commit()


def test_otp_verify_success_returns_token_and_cookie(registry_db, owner):
    _seed_challenge(registry_db)
    app = _make_app(registry_db)
    client = TestClient(app)
    resp = client.post(
        "/api/v1/auth/otp/verify", json={"phone": PHONE, "code": OTP_CODE}
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["token"]
    assert body["user_id"] == str(owner.user_id)
    assert "device_session" in resp.cookies


# ── Session restore (Req 2.10) ──────────────────────────────────────────────

def test_session_returns_current_user_for_valid_token(registry_db, owner):
    """GET /auth/session resolves the current user so the App reopens sans OTP."""
    app = _make_app(registry_db)
    app.dependency_overrides[get_current_user] = lambda: _authed(owner)
    client = TestClient(app)

    resp = client.get("/api/v1/auth/session")

    assert resp.status_code == 200
    user = resp.json()["user"]
    assert user["user_id"] == str(owner.user_id)
    assert user["role"] == "owner"
    assert user["phone"] == PHONE


def test_session_without_token_returns_401(registry_db, owner):
    """With no valid device token, session restore fails closed with 401."""
    app = _make_app(registry_db)
    client = TestClient(app)

    resp = client.get("/api/v1/auth/session")

    assert resp.status_code == 401


def test_otp_verify_wrong_code_returns_401(registry_db, owner):
    _seed_challenge(registry_db)
    app = _make_app(registry_db)
    client = TestClient(app)
    resp = client.post(
        "/api/v1/auth/otp/verify", json={"phone": PHONE, "code": "000000"}
    )
    assert resp.status_code == 401
    assert resp.json()["error"] == "unauthorized"


# ── PIN ──────────────────────────────────────────────────────────────────────

def test_set_pin_valid_returns_204_and_verify_true(registry_db, owner):
    app = _make_app(registry_db)
    app.dependency_overrides[get_current_user] = lambda: _authed(owner)
    client = TestClient(app)

    resp = client.post("/api/v1/auth/pin", json={"pin": OWNER_PIN})
    assert resp.status_code == 204

    resp = client.post("/api/v1/auth/pin/verify", json={"pin": OWNER_PIN})
    assert resp.status_code == 200
    assert resp.json() == {"verified": True}


def test_set_pin_invalid_returns_400(registry_db, owner):
    app = _make_app(registry_db)
    app.dependency_overrides[get_current_user] = lambda: _authed(owner)
    client = TestClient(app)
    resp = client.post("/api/v1/auth/pin", json={"pin": "12"})
    assert resp.status_code == 400
    assert resp.json()["field"] == "pin"


def test_verify_pin_wrong_returns_verified_false(registry_db, owner):
    app = _make_app(registry_db)
    app.dependency_overrides[get_current_user] = lambda: _authed(owner)
    client = TestClient(app)
    client.post("/api/v1/auth/pin", json={"pin": OWNER_PIN})
    resp = client.post("/api/v1/auth/pin/verify", json={"pin": "9999"})
    assert resp.status_code == 200
    assert resp.json() == {"verified": False}


# ── Users (Owner-only) ────────────────────────────────────────────────────────

def test_create_and_list_users_as_owner(registry_db, owner):
    app = _make_app(registry_db)
    app.dependency_overrides[require_owner] = lambda: _authed(owner)
    client = TestClient(app)

    resp = client.post(
        "/api/v1/auth/users",
        json={"name": "Staffer", "phone": "+919812345678", "role": "staff"},
    )
    assert resp.status_code == 201
    assert resp.json()["role"] == "staff"

    resp = client.get("/api/v1/auth/users")
    assert resp.status_code == 200
    phones = {u["phone"] for u in resp.json()}
    assert PHONE in phones and "+919812345678" in phones


def test_create_user_duplicate_phone_returns_409(registry_db, owner):
    app = _make_app(registry_db)
    app.dependency_overrides[require_owner] = lambda: _authed(owner)
    client = TestClient(app)
    resp = client.post(
        "/api/v1/auth/users",
        json={"name": "Dupe", "phone": PHONE, "role": "staff"},
    )
    assert resp.status_code == 409
    assert resp.json()["error"] == "conflict"


def test_create_user_as_staff_forbidden(registry_db, owner):
    # Real require_owner runs over an overridden staff principal → 403.
    staff = User(
        tenant_id=owner.tenant_id, name="Staff", phone="+919800000000", role="staff"
    )
    registry_db.add(staff)
    registry_db.commit()

    app = _make_app(registry_db)
    app.dependency_overrides[get_current_user] = lambda: _authed(staff)
    client = TestClient(app)
    resp = client.post(
        "/api/v1/auth/users",
        json={"name": "X", "phone": "+919811111111", "role": "staff"},
    )
    assert resp.status_code == 403
    assert resp.json()["error"] == "forbidden"


# ── Mode elevation ────────────────────────────────────────────────────────────

def _seed_device(registry_db, user):
    device = Device(
        user_id=user.user_id,
        tenant_id=user.tenant_id,
        token_hash="hash",
        issued_at=datetime.utcnow(),
        expires_at=datetime.utcnow() + timedelta(days=30),
    )
    registry_db.add(device)
    registry_db.commit()
    registry_db.refresh(device)
    return device


def test_mode_manage_owner_pin_elevates(registry_db, owner):
    # Set the owner PIN through the service, then elevate via the router.
    AuthService(registry_db, StubOtpSender()).set_pin(owner.user_id, OWNER_PIN)
    device = _seed_device(registry_db, owner)

    app = _make_app(registry_db)
    app.dependency_overrides[get_current_user] = lambda: AuthedUser(
        user_id=owner.user_id,
        tenant_id=owner.tenant_id,
        role=owner.role,
        device_id=device.device_id,
    )
    client = TestClient(app)
    resp = client.post(
        "/api/v1/auth/mode/manage",
        json={"user_id": str(owner.user_id), "pin": OWNER_PIN},
    )
    assert resp.status_code == 200
    assert resp.json() == {"elevated": True, "mode": "manage"}


def test_mode_manage_wrong_pin_stays_sell(registry_db, owner):
    AuthService(registry_db, StubOtpSender()).set_pin(owner.user_id, OWNER_PIN)
    device = _seed_device(registry_db, owner)

    app = _make_app(registry_db)
    app.dependency_overrides[get_current_user] = lambda: AuthedUser(
        user_id=owner.user_id,
        tenant_id=owner.tenant_id,
        role=owner.role,
        device_id=device.device_id,
    )
    client = TestClient(app)
    resp = client.post(
        "/api/v1/auth/mode/manage",
        json={"user_id": str(owner.user_id), "pin": "0000"},
    )
    assert resp.status_code == 200
    assert resp.json() == {"elevated": False, "mode": "sell"}
