"""
Test the admin /delete command removes a tenant and its app-first auth rows.

Regression: with the app-first auth tables (users → tenants, devices →
users/tenants) and PRAGMA foreign_keys=ON in the registry, deleting only the
``tenants`` row fails with a FOREIGN KEY constraint error. The handler must
delete dependents (devices, webauthn_credentials, otp_challenges, users) first.
"""

from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401  (register business tables)
import app.auth.models_auth  # noqa: F401  (register auth tables)
from app.database import Base
from app.models import Tenant
from app.auth.models_auth import Device, OtpChallenge, User
from app.handlers.request_handler import RequestHandler


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


@pytest.fixture()
def patch_db(registry, monkeypatch):
    def _fake_reg():
        db = registry()
        try:
            yield db
        finally:
            db.close()

    monkeypatch.setattr("app.database.get_registry_db", _fake_reg)
    # No physical tenant DB file in the test.
    monkeypatch.setattr("app.database.get_tenant_db_path", lambda tid: None)
    return registry


def _make_handler():
    handler = RequestHandler.__new__(RequestHandler)
    handler._admin_target = {}
    return handler


def _seed_tenant_with_auth(SessionLocal, chat_id="9000001"):
    db = SessionLocal()
    try:
        tenant = Tenant(chat_id=chat_id, business_name="Del Co")
        db.add(tenant)
        db.commit()
        db.refresh(tenant)

        user = User(
            tenant_id=tenant.tenant_id, name="Owner", phone="+919000000001", role="owner"
        )
        db.add(user)
        db.commit()
        db.refresh(user)

        db.add(
            Device(
                user_id=user.user_id,
                tenant_id=tenant.tenant_id,
                token_hash="a" * 16,
                expires_at=datetime.utcnow() + timedelta(days=30),
            )
        )
        db.add(
            OtpChallenge(
                phone="+919000000001",
                code_hash="h" * 16,
                expires_at=datetime.utcnow() + timedelta(minutes=5),
            )
        )
        db.commit()
        return tenant.tenant_id
    finally:
        db.close()


def _counts(SessionLocal, tenant_id):
    db = SessionLocal()
    try:
        return {
            "tenants": db.query(Tenant).filter(Tenant.tenant_id == tenant_id).count(),
            "users": db.query(User).filter(User.tenant_id == tenant_id).count(),
            "devices": db.query(Device).filter(Device.tenant_id == tenant_id).count(),
        }
    finally:
        db.close()


def test_delete_removes_tenant_and_dependent_auth_rows(patch_db):
    SessionLocal = patch_db
    tenant_id = _seed_tenant_with_auth(SessionLocal, chat_id="9000001")
    handler = _make_handler()

    result = handler._handle_delete_command("admin-chat", "/delete 9000001")

    assert "has been deleted" in result
    counts = _counts(SessionLocal, tenant_id)
    assert counts == {"tenants": 0, "users": 0, "devices": 0}


def test_delete_unknown_tenant_reports_not_found(patch_db):
    handler = _make_handler()
    result = handler._handle_delete_command("admin-chat", "/delete does-not-exist")
    assert "No tenant found" in result


def test_delete_usage_when_no_id(patch_db):
    handler = _make_handler()
    result = handler._handle_delete_command("admin-chat", "/delete")
    assert "Usage" in result
