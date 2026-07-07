"""
Unit tests for the app-first API auth dependencies (task 4.2).

These exercise `app/api/deps.py`:
- get_current_user: token extraction (Bearer header / httpOnly cookie),
  registry Device lookup by token hash, unexpired/unrevoked checks, user/role/
  tenant resolution, and fail-closed 401s (Req 2.10, 2.11, 5.8, 19.4, 19.5).
- require_owner: 403 unless the resolved user is an Owner (Req 5.4, 5.6, 5.7).
- get_tenant_db_for_user: yields a session scoped to the token-derived tenant
  (Req 19.2, 19.6).

Registry lookups run against an in-memory SQLite DB; `app.database.get_registry_db`
and `get_db` are monkeypatched to bind to it so no real per-tenant files are opened.
"""

from datetime import datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

# Register all model tables on the shared Base.
import app.models  # noqa: F401
from app.database import Base
from app.models import Tenant
from app.auth.models_auth import User, Device
from app.auth import security

from app.api import deps
from app.api.deps import (
    AuthedUser,
    get_current_user,
    require_owner,
    get_tenant_db_for_user,
    DEVICE_TOKEN_COOKIE,
)
from app.api.errors import UnauthorizedError, ForbiddenError


PHONE = "+919876543210"
RAW_TOKEN = "raw-device-session-token-abc123"


@pytest.fixture()
def registry_db():
    engine = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}
    )

    @event.listens_for(engine, "connect")
    def _fk(conn, _):
        conn.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine)
    session = SessionLocal()
    yield session
    session.close()
    Base.metadata.drop_all(engine)
    engine.dispose()


@pytest.fixture()
def patch_dbs(monkeypatch, registry_db):
    """Point deps.get_registry_db and deps.get_db at the in-memory session."""

    def _fake_registry_db():
        # Yield the same session; deps closes it, so guard close() to keep the
        # fixture-managed session usable for assertions afterwards.
        yield registry_db

    def _fake_get_db(tenant_id=None):
        yield registry_db

    # deps.py imports these names directly, so patch them on the deps module.
    monkeypatch.setattr(deps, "get_registry_db", _fake_registry_db)
    monkeypatch.setattr(deps, "get_db", _fake_get_db)
    # Prevent the real close() from detaching objects we still assert on.
    monkeypatch.setattr(registry_db, "close", lambda: None)
    return registry_db


def _seed_user_and_device(
    db,
    *,
    role="owner",
    expires_delta=timedelta(days=30),
    revoked=False,
    token=RAW_TOKEN,
):
    tenant = Tenant(chat_id="chat_x")
    db.add(tenant)
    db.commit()

    user = User(tenant_id=tenant.tenant_id, name="U", phone=PHONE, role=role)
    db.add(user)
    db.commit()

    device = Device(
        user_id=user.user_id,
        tenant_id=tenant.tenant_id,
        token_hash=security.hash_token_for_storage(token),
        expires_at=datetime.utcnow() + expires_delta,
        revoked_at=datetime.utcnow() if revoked else None,
    )
    db.add(device)
    db.commit()
    return tenant, user, device


def _request(*, bearer=None, cookie=None):
    """Build a minimal object exposing the .headers / .cookies deps reads."""
    headers = {}
    if bearer is not None:
        headers["Authorization"] = f"Bearer {bearer}"
    cookies = {}
    if cookie is not None:
        cookies[DEVICE_TOKEN_COOKIE] = cookie
    return SimpleNamespace(headers=headers, cookies=cookies)


# ── get_current_user: happy paths ─────────────────────────────────────────────

def test_bearer_token_resolves_authed_user(patch_dbs):
    tenant, user, device = _seed_user_and_device(patch_dbs, role="owner")

    result = get_current_user(_request(bearer=RAW_TOKEN))

    assert isinstance(result, AuthedUser)
    assert result.user_id == user.user_id
    assert result.tenant_id == tenant.tenant_id
    assert result.role == "owner"
    assert result.device_id == device.device_id


def test_cookie_token_resolves_authed_user(patch_dbs):
    tenant, user, _ = _seed_user_and_device(patch_dbs, role="staff")

    result = get_current_user(_request(cookie=RAW_TOKEN))

    assert result.tenant_id == tenant.tenant_id
    assert result.role == "staff"


# ── get_current_user: fail-closed 401s ────────────────────────────────────────

def test_missing_token_raises_401(patch_dbs):
    with pytest.raises(UnauthorizedError):
        get_current_user(_request())


def test_unknown_token_raises_401(patch_dbs):
    _seed_user_and_device(patch_dbs)
    with pytest.raises(UnauthorizedError):
        get_current_user(_request(bearer="some-other-token"))


def test_expired_device_raises_401(patch_dbs):
    _seed_user_and_device(patch_dbs, expires_delta=timedelta(seconds=-1))
    with pytest.raises(UnauthorizedError):
        get_current_user(_request(bearer=RAW_TOKEN))


def test_revoked_device_raises_401(patch_dbs):
    _seed_user_and_device(patch_dbs, revoked=True)
    with pytest.raises(UnauthorizedError):
        get_current_user(_request(bearer=RAW_TOKEN))


def test_invalid_role_fails_closed_401(patch_dbs):
    _seed_user_and_device(patch_dbs, role="superadmin")
    with pytest.raises(UnauthorizedError):
        get_current_user(_request(bearer=RAW_TOKEN))


def test_malformed_authorization_header_treated_as_missing(patch_dbs):
    _seed_user_and_device(patch_dbs)
    req = SimpleNamespace(headers={"Authorization": RAW_TOKEN}, cookies={})
    with pytest.raises(UnauthorizedError):
        get_current_user(req)


# ── require_owner ─────────────────────────────────────────────────────────────

def test_require_owner_allows_owner():
    owner = AuthedUser(uuid4(), uuid4(), "owner", uuid4())
    assert require_owner(owner) is owner


def test_require_owner_rejects_staff():
    staff = AuthedUser(uuid4(), uuid4(), "staff", uuid4())
    with pytest.raises(ForbiddenError):
        require_owner(staff)


# ── get_tenant_db_for_user ────────────────────────────────────────────────────

def test_tenant_db_scoped_to_token_tenant(patch_dbs, monkeypatch):
    tenant_id = uuid4()
    captured = {}

    def _fake_get_db(tid=None):
        captured["tid"] = tid
        yield patch_dbs

    monkeypatch.setattr(deps, "get_db", _fake_get_db)

    user = AuthedUser(uuid4(), tenant_id, "owner", uuid4())
    gen = get_tenant_db_for_user(user)
    session = next(gen)
    assert session is patch_dbs
    # Tenant passed to get_db comes strictly from the AuthedUser (Req 19.2/19.6).
    assert captured["tid"] == tenant_id
    gen.close()
