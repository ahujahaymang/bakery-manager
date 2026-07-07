"""
Tests for end-to-end owner onboarding (app/handlers/request_handler.py).

These cover the new behaviour introduced when onboarding stopped gating on
manual admin approval and instead:
  - captures a sign-in phone number,
  - creates exactly one Owner ``User`` in the registry DB,
  - auto-starts the 7-day trial,
  - returns a welcome message containing the PWA link (settings.APP_URL).

The registry DB is an in-memory SQLite bound via monkeypatching
``app.database.get_registry_db`` (mirroring tests/test_auth_router.py). A
StaticPool keeps the in-memory DB visible across the fresh sessions each
handler method opens. ``RequestHandler`` is built without running its heavy
``__init__`` (which would construct LLM clients) — we only wire the attributes
the onboarding path needs, plus a stub AdminNotifier.
"""

from collections import defaultdict
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401  (register business tables on the shared Base)
import app.auth.models_auth  # noqa: F401  (register auth tables — users, etc.)
from app.database import Base
from app.models import Tenant
from app.auth.models_auth import User
from app.config import settings
from app.handlers.request_handler import RequestHandler


class StubAdminNotifier:
    """Records signup notifications instead of sending them."""

    def __init__(self):
        self.signups = []

    async def notify_new_signup(self, chat_id, business_name, country):
        self.signups.append((chat_id, business_name, country))


@pytest.fixture()
def registry():
    """In-memory registry engine + session factory, patched into get_registry_db."""
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


@pytest.fixture()
def patch_registry(registry, monkeypatch):
    """Point app.database.get_registry_db at the in-memory registry."""

    def _fake_get_registry_db():
        db = registry()
        try:
            yield db
        finally:
            db.close()

    monkeypatch.setattr("app.database.get_registry_db", _fake_get_registry_db)
    # Keep admin notifications a no-op path (no configured admin chat).
    monkeypatch.setattr(settings, "ADMIN_CHAT_ID", "", raising=False)
    return registry


def _make_handler():
    """Build a RequestHandler without its heavy __init__ (LLM clients, etc.)."""
    handler = RequestHandler.__new__(RequestHandler)
    handler.admin_notifier = StubAdminNotifier()
    handler._history = defaultdict(list)
    handler._pending_images = {}
    handler._last_error = {}
    handler._admin_target = {}
    # Instance-level onboarding state so tests don't share class-level dicts.
    handler._awaiting_business_name = {}
    handler._awaiting_country = {}
    handler._awaiting_phone = {}
    handler._awaiting_app_phone = {}
    return handler


def _make_tenant(SessionLocal, chat_id, platform="telegram", business_name="Priya's Kitchen", country="India"):
    db = SessionLocal()
    try:
        tenant = Tenant(
            chat_id=chat_id,
            business_name=business_name,
            country=country,
            messaging_platform=platform,
            subscription_status="pending",
        )
        db.add(tenant)
        db.commit()
        db.refresh(tenant)
        return tenant.tenant_id
    finally:
        db.close()


def _owners(SessionLocal, tenant_id):
    db = SessionLocal()
    try:
        return db.query(User).filter(
            User.tenant_id == tenant_id, User.role == "owner"
        ).all()
    finally:
        db.close()


# ── _finalize_onboarding ─────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_finalize_creates_owner_starts_trial_and_returns_app_url(patch_registry, monkeypatch):
    SessionLocal = patch_registry
    # Configure an admin so the signup-notification path runs.
    monkeypatch.setattr(settings, "ADMIN_CHAT_ID", "admin-chat", raising=False)
    tenant_id = _make_tenant(SessionLocal, chat_id="tg-12345", platform="telegram")
    handler = _make_handler()

    msg = await handler._finalize_onboarding(tenant_id, "+91 98765 43210", chat_id="tg-12345")

    # Exactly one Owner user, with the normalized captured phone.
    owners = _owners(SessionLocal, tenant_id)
    assert len(owners) == 1
    assert owners[0].phone == "+919876543210"
    assert owners[0].name == "Priya's Kitchen"

    # Trial started — status "trial", ~7 days remaining.
    from app.services.tenant_service import TenantService
    db = SessionLocal()
    try:
        svc = TenantService(db)
        tenant = svc.get_tenant_by_id(tenant_id)
        assert tenant.subscription_status == "trial"
        assert svc.days_remaining(tenant_id) in (6, 7)
    finally:
        db.close()

    # Welcome message includes the PWA link and sign-in guidance.
    assert settings.APP_URL in msg
    assert "+919876543210" in msg
    assert "one-time code" in msg
    # Admin was still notified.
    assert handler.admin_notifier.signups


@pytest.mark.asyncio
async def test_finalize_is_idempotent_no_duplicate_owner(patch_registry):
    SessionLocal = patch_registry
    tenant_id = _make_tenant(SessionLocal, chat_id="tg-999", platform="telegram")
    handler = _make_handler()

    await handler._finalize_onboarding(tenant_id, "+919876543210", chat_id="tg-999")
    await handler._finalize_onboarding(tenant_id, "+919876543210", chat_id="tg-999")

    owners = _owners(SessionLocal, tenant_id)
    assert len(owners) == 1


# ── phone step ───────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_invalid_phone_is_reprompted(patch_registry):
    SessionLocal = patch_registry
    tenant_id = _make_tenant(SessionLocal, chat_id="tg-777", platform="telegram")
    handler = _make_handler()
    handler._awaiting_phone["tg-777"] = True

    msg = await handler._save_phone(tenant_id, "tg-777", "not-a-phone")

    # Still awaiting phone, no owner created, and a re-prompt returned.
    assert handler._awaiting_phone.get("tg-777") is True
    assert _owners(SessionLocal, tenant_id) == []
    assert "valid phone" in msg.lower()


@pytest.mark.asyncio
async def test_valid_phone_step_finalizes(patch_registry):
    SessionLocal = patch_registry
    tenant_id = _make_tenant(SessionLocal, chat_id="tg-555", platform="telegram")
    handler = _make_handler()
    handler._awaiting_phone["tg-555"] = True

    msg = await handler._save_phone(tenant_id, "tg-555", "+91 98765 00000")

    assert "tg-555" not in handler._awaiting_phone
    owners = _owners(SessionLocal, tenant_id)
    assert len(owners) == 1 and owners[0].phone == "+919876500000"
    assert settings.APP_URL in msg


# ── country step → phone question / WhatsApp skip ────────────────────────────

@pytest.mark.asyncio
async def test_country_step_asks_for_phone_on_telegram(patch_registry):
    SessionLocal = patch_registry
    tenant_id = _make_tenant(
        SessionLocal, chat_id="tg-321", platform="telegram", country=None
    )
    handler = _make_handler()
    handler._awaiting_country["tg-321"] = True

    msg = await handler._complete_onboarding(tenant_id, "tg-321", "India")

    # Now waiting for phone; no owner yet; the question is asked.
    assert handler._awaiting_phone.get("tg-321") is True
    assert _owners(SessionLocal, tenant_id) == []
    assert "phone number" in msg.lower()


@pytest.mark.asyncio
async def test_whatsapp_tenant_skips_phone_question(patch_registry):
    SessionLocal = patch_registry
    # WhatsApp tenants carry the owner's phone as chat_id.
    tenant_id = _make_tenant(
        SessionLocal, chat_id="+919812345678", platform="whatsapp", country=None
    )
    handler = _make_handler()
    handler._awaiting_country["+919812345678"] = True

    msg = await handler._complete_onboarding(tenant_id, "+919812345678", "India")

    # No phone question — finalised directly using the chat_id phone.
    assert "+919812345678" not in handler._awaiting_phone
    owners = _owners(SessionLocal, tenant_id)
    assert len(owners) == 1 and owners[0].phone == "+919812345678"
    assert settings.APP_URL in msg
    assert "WhatsApp" in msg


# ── existing-owner app migration ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_has_owner_login_reflects_registry(patch_registry):
    SessionLocal = patch_registry
    tenant_id = _make_tenant(SessionLocal, chat_id="tg-mig-1", platform="telegram")
    handler = _make_handler()

    # No Owner user yet → False.
    assert await handler._has_owner_login(tenant_id) is False

    # Create one → True.
    db = SessionLocal()
    try:
        db.add(User(tenant_id=tenant_id, name="Priya's Kitchen", phone="+919999999999", role="owner"))
        db.commit()
    finally:
        db.close()
    assert await handler._has_owner_login(tenant_id) is True


@pytest.mark.asyncio
async def test_app_migration_creates_owner_and_returns_link_without_trial(patch_registry):
    SessionLocal = patch_registry
    # Existing owner with an "active" subscription — migration must not reset it.
    tenant_id = _make_tenant(SessionLocal, chat_id="tg-mig-2", platform="telegram")
    db = SessionLocal()
    try:
        from app.services.tenant_service import TenantService
        t = TenantService(db).get_tenant_by_id(tenant_id)
        t.subscription_status = "active"
        db.add(t)
        db.commit()
    finally:
        db.close()

    handler = _make_handler()
    msg = await handler._finalize_app_migration(tenant_id, "+91 98765 43210", chat_id="tg-mig-2")

    owners = _owners(SessionLocal, tenant_id)
    assert len(owners) == 1 and owners[0].phone == "+919876543210"
    assert settings.APP_URL in msg
    assert "+919876543210" in msg

    # Subscription status untouched (no trial reset).
    db = SessionLocal()
    try:
        from app.services.tenant_service import TenantService
        assert TenantService(db).get_tenant_by_id(tenant_id).subscription_status == "active"
    finally:
        db.close()


@pytest.mark.asyncio
async def test_app_migration_is_idempotent(patch_registry):
    SessionLocal = patch_registry
    tenant_id = _make_tenant(SessionLocal, chat_id="tg-mig-3", platform="telegram")
    handler = _make_handler()

    await handler._finalize_app_migration(tenant_id, "+919876543210", chat_id="tg-mig-3")
    await handler._finalize_app_migration(tenant_id, "+919876543210", chat_id="tg-mig-3")

    assert len(_owners(SessionLocal, tenant_id)) == 1


@pytest.mark.asyncio
async def test_app_phone_invalid_is_reprompted(patch_registry):
    SessionLocal = patch_registry
    tenant_id = _make_tenant(SessionLocal, chat_id="tg-mig-4", platform="telegram")
    handler = _make_handler()
    handler._awaiting_app_phone["tg-mig-4"] = True

    msg = await handler._save_app_phone(tenant_id, "tg-mig-4", "nope")

    assert handler._awaiting_app_phone.get("tg-mig-4") is True
    assert _owners(SessionLocal, tenant_id) == []
    assert "valid phone" in msg.lower()


@pytest.mark.asyncio
async def test_app_phone_valid_creates_login(patch_registry):
    SessionLocal = patch_registry
    tenant_id = _make_tenant(SessionLocal, chat_id="tg-mig-5", platform="telegram")
    handler = _make_handler()
    handler._awaiting_app_phone["tg-mig-5"] = True

    msg = await handler._save_app_phone(tenant_id, "tg-mig-5", "+91 98765 00000")

    assert "tg-mig-5" not in handler._awaiting_app_phone
    owners = _owners(SessionLocal, tenant_id)
    assert len(owners) == 1 and owners[0].phone == "+919876500000"
    assert settings.APP_URL in msg
