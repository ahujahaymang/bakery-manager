"""
Property-based tests for owner onboarding finalisation
(``app/handlers/request_handler.py``).

These cover **Requirement 21.1 / 21.2**: completing onboarding creates exactly
one Owner ``User`` bound to the tenant (with the normalised sign-in phone),
auto-starts the 7-day trial, and returns a welcome message containing the PWA
link (``settings.APP_URL``) — and does so idempotently.

They reuse the ``tests/test_onboarding.py`` harness: an in-memory ``StaticPool``
registry patched into ``app.database.get_registry_db``, a stub ``AdminNotifier``,
and a ``RequestHandler`` built via ``__new__`` (skipping the heavy ``__init__``).

``_finalize_onboarding`` is ``async``; since mixing hypothesis' ``@given`` with
``@pytest.mark.asyncio`` is awkward, each example drives the coroutine with
``asyncio.run`` inside a plain synchronous hypothesis test. The registry is
rebuilt per example so Owner-user counts are isolated between examples.
"""

import asyncio
import string
from collections import defaultdict
from unittest import mock

from hypothesis import given, settings as hyp_settings, strategies as st
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
from app.handlers.request_handler import RequestHandler, _normalize_phone
from app.services.tenant_service import TenantService


class StubAdminNotifier:
    def __init__(self):
        self.signups = []

    async def notify_new_signup(self, chat_id, business_name, country):
        self.signups.append((chat_id, business_name, country))


@pytest.fixture(autouse=True)
def _no_admin(monkeypatch):
    """Keep the admin-notification path a no-op (no configured admin chat)."""
    monkeypatch.setattr(settings, "ADMIN_CHAT_ID", "", raising=False)


# ── Per-example in-memory registry (fresh + isolated) ────────────────────────

def _fresh_registry():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(engine, "connect")
    def _fk(conn, _):  # noqa: ANN001
        conn.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine)
    return engine, SessionLocal


def _fake_registry_getter(SessionLocal):
    def _fake_get_registry_db():
        db = SessionLocal()
        try:
            yield db
        finally:
            db.close()

    return _fake_get_registry_db


def _make_handler():
    handler = RequestHandler.__new__(RequestHandler)
    handler.admin_notifier = StubAdminNotifier()
    handler._history = defaultdict(list)
    handler._pending_images = {}
    handler._last_error = {}
    handler._admin_target = {}
    handler._awaiting_business_name = {}
    handler._awaiting_country = {}
    handler._awaiting_phone = {}
    return handler


def _make_tenant(SessionLocal, chat_id, business_name):
    db = SessionLocal()
    try:
        tenant = Tenant(
            chat_id=chat_id,
            business_name=business_name,
            country="India",
            messaging_platform="telegram",
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
        return (
            db.query(User)
            .filter(User.tenant_id == tenant_id, User.role == "owner")
            .all()
        )
    finally:
        db.close()


def _finalize(handler, SessionLocal, tenant_id, phone, chat_id):
    """Run the async finaliser with the per-example registry patched in."""
    with mock.patch(
        "app.database.get_registry_db", _fake_registry_getter(SessionLocal)
    ):
        return asyncio.run(
            handler._finalize_onboarding(tenant_id, phone, chat_id=chat_id)
        )


# ── Strategies ───────────────────────────────────────────────────────────────

_SEPARATORS = [" ", "-", "(", ")", ".", ""]


@st.composite
def phones(draw):
    """A messy display phone plus its expected normalised form.

    Optional leading ``+``, 8..15 digits, with spaces/dashes/parens/dots that
    ``_normalize_phone`` strips away (the leading ``+`` is preserved).
    """
    n = draw(st.integers(min_value=8, max_value=15))
    digits = "".join(draw(st.lists(st.sampled_from(string.digits), min_size=n, max_size=n)))
    plus = draw(st.booleans())
    expected = ("+" if plus else "") + digits

    # Interleave separators around each digit (never inserting another '+').
    seps = draw(
        st.lists(st.sampled_from(_SEPARATORS), min_size=n + 1, max_size=n + 1)
    )
    messy = "+" if plus else ""
    for i, d in enumerate(digits):
        messy += seps[i] + d
    messy += seps[-1]
    return messy, expected


_business = (
    st.text(alphabet=string.ascii_letters + string.digits + " ", min_size=1, max_size=100)
    .map(str.strip)
    .filter(lambda s: len(s) > 0)
)


# ── Property C — onboarding creates exactly one trial Owner with the app link ─

# Feature: app-first-pivot, Property C: onboarding creates exactly one Owner User
# with the normalised phone, starts a 7-day trial, and returns settings.APP_URL.
# Validates Req 21.1, 21.2.
@hyp_settings(max_examples=100, deadline=None)
@given(phone=phones(), business=_business)
def test_property_c_finalize_creates_single_trial_owner(phone, business):
    messy_phone, expected_phone = phone
    engine, SessionLocal = _fresh_registry()
    try:
        tenant_id = _make_tenant(SessionLocal, chat_id="tg-pbt", business_name=business)
        handler = _make_handler()

        msg = _finalize(handler, SessionLocal, tenant_id, messy_phone, chat_id="tg-pbt")

        owners = _owners(SessionLocal, tenant_id)
        assert len(owners) == 1
        assert owners[0].phone == expected_phone == _normalize_phone(messy_phone)

        db = SessionLocal()
        try:
            svc = TenantService(db)
            tenant = svc.get_tenant_by_id(tenant_id)
            assert tenant.subscription_status == "trial"
            assert svc.days_remaining(tenant_id) in (6, 7)
        finally:
            db.close()

        assert settings.APP_URL in msg
    finally:
        Base.metadata.drop_all(engine)
        engine.dispose()


# ── Property D — idempotent: finalising twice yields one Owner ────────────────

# Feature: app-first-pivot, Property D: repeating onboarding for the same
# (tenant, phone) does not create a duplicate Owner User. Validates Req 21.1.
@hyp_settings(max_examples=100, deadline=None)
@given(phone=phones(), business=_business)
def test_property_d_finalize_is_idempotent(phone, business):
    messy_phone, expected_phone = phone
    engine, SessionLocal = _fresh_registry()
    try:
        tenant_id = _make_tenant(SessionLocal, chat_id="tg-pbt", business_name=business)
        handler = _make_handler()

        _finalize(handler, SessionLocal, tenant_id, messy_phone, chat_id="tg-pbt")
        _finalize(handler, SessionLocal, tenant_id, messy_phone, chat_id="tg-pbt")

        owners = _owners(SessionLocal, tenant_id)
        assert len(owners) == 1
        assert owners[0].phone == expected_phone
    finally:
        Base.metadata.drop_all(engine)
        engine.dispose()
