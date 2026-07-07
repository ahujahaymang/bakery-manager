"""
Unit tests for AuthService user management + mode elevation (app-first pivot, task 3.5).

These exercise the Owner-only user management flow (``create_user`` /
``list_users``) and Manage_Mode elevation / user switching (``elevate_to_manage``)
against an in-memory registry DB. Coverage targets the Req 5.1/5.5 user rules
and the Req 4.9/6.3–6.7 elevation rules:

- create_user: Owner-only, one role, tenant binding, per-tenant phone uniqueness
- list_users: Owner-only, tenant-scoped
- elevate_to_manage: Owner-role PIN/WebAuthn required, 30s lockout after 5
  failures, PIN/WebAuthn user switching without OTP
"""

from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

# Import both model modules so all tables register on the shared Base.
import app.models  # noqa: F401
from app.database import Base
from app.models import Tenant
from app.auth.models_auth import Device, User
from app.auth import auth_service as auth_module
from app.auth.auth_service import (
    AuthService,
    DeliveryResult,
    DuplicateUserError,
    InvalidPhoneError,
    InvalidRoleError,
    InvalidUserError,
    ManageLockedError,
    PermissionDeniedError,
    UnknownDeviceError,
    UnknownUserError,
    _MANAGE_LOCKOUT,
    _MAX_MANAGE_ATTEMPTS,
)


OWNER_PHONE = "+919876543210"
STAFF_PHONE = "+919812345678"
OWNER_PIN = "1357"
STAFF_PIN = "2468"


class StubOtpSender:
    """Delivery stub — these tests never send OTPs, but AuthService requires one."""

    async def send(self, phone, code):
        return DeliveryResult(success=True, channel="telegram")


@dataclass
class Principal:
    """Duck-typed acting principal (mirrors the API layer's AuthedUser)."""
    role: str
    tenant_id: UUID
    user_id: UUID


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


@pytest.fixture(autouse=True)
def _clear_manage_lockouts():
    """Manage_Mode lockout state is process-local; reset it around each test."""
    auth_module._manage_lockouts.clear()
    yield
    auth_module._manage_lockouts.clear()


@pytest.fixture()
def tenant(registry_db):
    t = Tenant(chat_id="chat_owner")
    registry_db.add(t)
    registry_db.commit()
    registry_db.refresh(t)
    return t


@pytest.fixture()
def owner(registry_db, tenant):
    u = User(tenant_id=tenant.tenant_id, name="Owner", phone=OWNER_PHONE, role="owner")
    registry_db.add(u)
    registry_db.commit()
    registry_db.refresh(u)
    return u


@pytest.fixture()
def owner_principal(owner):
    return Principal(role="owner", tenant_id=owner.tenant_id, user_id=owner.user_id)


@pytest.fixture()
def svc(registry_db):
    return AuthService(registry_db, StubOtpSender())


def _make_device(registry_db, user, *, revoked=False, expired=False):
    now = datetime.utcnow()
    device = Device(
        user_id=user.user_id,
        tenant_id=user.tenant_id,
        token_hash="hash-" + uuid4().hex,
        issued_at=now,
        expires_at=now - timedelta(days=1) if expired else now + timedelta(days=30),
        revoked_at=now if revoked else None,
    )
    registry_db.add(device)
    registry_db.commit()
    registry_db.refresh(device)
    return device


# ── create_user (Req 5.1, 5.5) ────────────────────────────────────────────────

def test_create_user_owner_creates_staff_in_owner_tenant(svc, registry_db, owner_principal):
    created = svc.create_user(owner_principal, "Aditi", STAFF_PHONE, "staff")
    assert created.role == "staff"
    assert created.name == "Aditi"
    assert created.phone == STAFF_PHONE
    # Req 5.5: new user bound to the Owner's tenant.
    assert created.tenant_id == owner_principal.tenant_id


def test_create_user_owner_can_create_another_owner(svc, owner_principal):
    created = svc.create_user(owner_principal, "Co-Owner", STAFF_PHONE, "owner")
    assert created.role == "owner"
    assert created.tenant_id == owner_principal.tenant_id


def test_create_user_normalizes_phone_before_storage(svc, owner_principal):
    created = svc.create_user(owner_principal, "Aditi", "+91 98123-45678", "staff")
    assert created.phone == STAFF_PHONE  # separators stripped


def test_create_user_rejects_non_owner(svc, owner):
    staff_principal = Principal(role="staff", tenant_id=owner.tenant_id, user_id=uuid4())
    with pytest.raises(PermissionDeniedError):
        svc.create_user(staff_principal, "Aditi", STAFF_PHONE, "staff")


def test_create_user_rejects_missing_role_principal(svc, owner):
    no_role = Principal(role="", tenant_id=owner.tenant_id, user_id=uuid4())
    with pytest.raises(PermissionDeniedError):
        svc.create_user(no_role, "Aditi", STAFF_PHONE, "staff")


@pytest.mark.parametrize("bad_name", ["", "   "])
def test_create_user_rejects_empty_name(svc, owner_principal, bad_name):
    with pytest.raises(InvalidUserError):
        svc.create_user(owner_principal, bad_name, STAFF_PHONE, "staff")


@pytest.mark.parametrize("bad_role", ["admin", "OWNER", "manager", "", "owner staff"])
def test_create_user_rejects_invalid_role(svc, owner_principal, bad_role):
    with pytest.raises(InvalidRoleError):
        svc.create_user(owner_principal, "Aditi", STAFF_PHONE, bad_role)


@pytest.mark.parametrize("bad_phone", ["123", "not-a-phone", "+12"])
def test_create_user_rejects_invalid_phone(svc, owner_principal, bad_phone):
    with pytest.raises(InvalidPhoneError):
        svc.create_user(owner_principal, "Aditi", bad_phone, "staff")


def test_create_user_rejects_duplicate_phone_in_tenant(svc, owner_principal):
    svc.create_user(owner_principal, "Aditi", STAFF_PHONE, "staff")
    # Req 5.1: (tenant_id, phone) uniqueness — a second insert is rejected.
    with pytest.raises(DuplicateUserError):
        svc.create_user(owner_principal, "Someone Else", STAFF_PHONE, "staff")


def test_create_user_same_phone_allowed_in_different_tenant(svc, registry_db, owner_principal):
    # A different tenant may reuse the phone (uniqueness is per-tenant).
    other_tenant = Tenant(chat_id="chat_other")
    registry_db.add(other_tenant)
    registry_db.commit()
    registry_db.refresh(other_tenant)
    other_owner = Principal(role="owner", tenant_id=other_tenant.tenant_id, user_id=uuid4())

    svc.create_user(owner_principal, "Aditi", STAFF_PHONE, "staff")
    created = svc.create_user(other_owner, "Bhavna", STAFF_PHONE, "staff")
    assert created.tenant_id == other_tenant.tenant_id


# ── list_users (Req 5.5) ──────────────────────────────────────────────────────

def test_list_users_owner_lists_tenant_users(svc, owner, owner_principal):
    svc.create_user(owner_principal, "Aditi", STAFF_PHONE, "staff")
    users = svc.list_users(owner_principal)
    phones = {u.phone for u in users}
    assert OWNER_PHONE in phones and STAFF_PHONE in phones
    assert all(u.tenant_id == owner_principal.tenant_id for u in users)


def test_list_users_scoped_to_owner_tenant(svc, registry_db, owner, owner_principal):
    # A user in another tenant must not leak into this Owner's listing.
    other_tenant = Tenant(chat_id="chat_other")
    registry_db.add(other_tenant)
    registry_db.commit()
    registry_db.refresh(other_tenant)
    registry_db.add(
        User(tenant_id=other_tenant.tenant_id, name="Outsider", phone=STAFF_PHONE, role="owner")
    )
    registry_db.commit()

    users = svc.list_users(owner_principal)
    assert all(u.tenant_id == owner_principal.tenant_id for u in users)
    assert "Outsider" not in {u.name for u in users}


def test_list_users_rejects_non_owner(svc, owner):
    staff_principal = Principal(role="staff", tenant_id=owner.tenant_id, user_id=uuid4())
    with pytest.raises(PermissionDeniedError):
        svc.list_users(staff_principal)


# ── elevate_to_manage: Manage_Mode elevation (Req 6.3, 6.6, 6.7) ──────────────

def test_elevate_to_manage_owner_pin_succeeds(svc, registry_db, owner):
    svc.set_pin(owner.user_id, OWNER_PIN)
    device = _make_device(registry_db, owner)
    assert svc.elevate_to_manage(device.device_id, {"user_id": owner.user_id, "pin": OWNER_PIN}) is True


def test_elevate_to_manage_wrong_pin_returns_false(svc, registry_db, owner):
    svc.set_pin(owner.user_id, OWNER_PIN)
    device = _make_device(registry_db, owner)
    assert svc.elevate_to_manage(device.device_id, {"user_id": owner.user_id, "pin": "0000"}) is False


def test_elevate_to_manage_staff_owner_pin_rejected_as_permission(svc, registry_db, owner, owner_principal):
    # A Staff user with a correct PIN cannot enter Manage_Mode (Req 6.3).
    staff = svc.create_user(owner_principal, "Aditi", STAFF_PHONE, "staff")
    svc.set_pin(staff.user_id, STAFF_PIN)
    device = _make_device(registry_db, owner)
    with pytest.raises(PermissionDeniedError):
        svc.elevate_to_manage(device.device_id, {"user_id": staff.user_id, "pin": STAFF_PIN})


def test_elevate_to_manage_locks_out_after_five_failures(svc, registry_db, owner):
    svc.set_pin(owner.user_id, OWNER_PIN)
    device = _make_device(registry_db, owner)
    # Use a user with no lockout interference: 5 failed elevations arm the 30s lock.
    for _ in range(_MAX_MANAGE_ATTEMPTS):
        svc.elevate_to_manage(device.device_id, {"user_id": owner.user_id, "pin": "0000"})
    with pytest.raises(ManageLockedError) as exc:
        svc.elevate_to_manage(device.device_id, {"user_id": owner.user_id, "pin": OWNER_PIN})
    assert 0 < exc.value.retry_after <= int(_MANAGE_LOCKOUT.total_seconds()) + 1


def test_elevate_to_manage_success_resets_failure_counter(svc, registry_db, owner):
    svc.set_pin(owner.user_id, OWNER_PIN)
    device = _make_device(registry_db, owner)
    # A few failures then a success clears the counter (no lock on next failure).
    for _ in range(_MAX_MANAGE_ATTEMPTS - 1):
        svc.elevate_to_manage(device.device_id, {"user_id": owner.user_id, "pin": "0000"})
    assert svc.elevate_to_manage(device.device_id, {"user_id": owner.user_id, "pin": OWNER_PIN}) is True
    # The device is not locked after the reset.
    assert device.device_id not in auth_module._manage_lockouts


def test_elevate_to_manage_permission_denied_does_not_count_as_failure(svc, registry_db, owner, owner_principal):
    # A verified non-Owner credential is a permission problem, not a lockout failure.
    staff = svc.create_user(owner_principal, "Aditi", STAFF_PHONE, "staff")
    svc.set_pin(staff.user_id, STAFF_PIN)
    device = _make_device(registry_db, owner)
    for _ in range(_MAX_MANAGE_ATTEMPTS):
        with pytest.raises(PermissionDeniedError):
            svc.elevate_to_manage(device.device_id, {"user_id": staff.user_id, "pin": STAFF_PIN})
    # No lockout armed despite repeated attempts (they never counted as failures).
    assert device.device_id not in auth_module._manage_lockouts


def test_elevate_to_manage_unknown_device_raises(svc):
    with pytest.raises(UnknownDeviceError):
        svc.elevate_to_manage(uuid4(), {"user_id": uuid4(), "pin": OWNER_PIN})


def test_elevate_to_manage_revoked_device_raises(svc, registry_db, owner):
    svc.set_pin(owner.user_id, OWNER_PIN)
    device = _make_device(registry_db, owner, revoked=True)
    with pytest.raises(UnknownDeviceError):
        svc.elevate_to_manage(device.device_id, {"user_id": owner.user_id, "pin": OWNER_PIN})


def test_elevate_to_manage_expired_device_raises(svc, registry_db, owner):
    svc.set_pin(owner.user_id, OWNER_PIN)
    device = _make_device(registry_db, owner, expired=True)
    with pytest.raises(UnknownDeviceError):
        svc.elevate_to_manage(device.device_id, {"user_id": owner.user_id, "pin": OWNER_PIN})


def test_elevate_to_manage_cross_tenant_user_rejected(svc, registry_db, owner):
    # A user from another tenant cannot elevate on this device (Req scoping).
    other_tenant = Tenant(chat_id="chat_other")
    registry_db.add(other_tenant)
    registry_db.commit()
    registry_db.refresh(other_tenant)
    outsider = User(tenant_id=other_tenant.tenant_id, name="Outsider", phone=STAFF_PHONE, role="owner")
    registry_db.add(outsider)
    registry_db.commit()
    registry_db.refresh(outsider)

    device = _make_device(registry_db, owner)
    with pytest.raises(PermissionDeniedError):
        svc.elevate_to_manage(device.device_id, {"user_id": outsider.user_id, "pin": OWNER_PIN})


def test_elevate_to_manage_unknown_user_raises(svc, registry_db, owner):
    device = _make_device(registry_db, owner)
    with pytest.raises(UnknownUserError):
        svc.elevate_to_manage(device.device_id, {"user_id": uuid4(), "pin": OWNER_PIN})


# ── elevate_to_manage: user switching without OTP (Req 4.9, 6.6) ──────────────

def test_switch_user_by_pin_succeeds_for_staff(svc, registry_db, owner, owner_principal):
    # Switching to any registered user by PIN needs no OTP and no Owner role.
    staff = svc.create_user(owner_principal, "Aditi", STAFF_PHONE, "staff")
    svc.set_pin(staff.user_id, STAFF_PIN)
    device = _make_device(registry_db, owner)
    ok = svc.elevate_to_manage(
        device.device_id, {"user_id": staff.user_id, "pin": STAFF_PIN, "purpose": "switch"}
    )
    assert ok is True


def test_switch_user_wrong_pin_returns_false(svc, registry_db, owner, owner_principal):
    staff = svc.create_user(owner_principal, "Aditi", STAFF_PHONE, "staff")
    svc.set_pin(staff.user_id, STAFF_PIN)
    device = _make_device(registry_db, owner)
    ok = svc.elevate_to_manage(
        device.device_id, {"user_id": staff.user_id, "pin": "0000", "purpose": "switch"}
    )
    assert ok is False


def test_switch_user_does_not_arm_manage_lockout(svc, registry_db, owner, owner_principal):
    # Switch failures are not Manage_Mode elevation failures (no 30s lock armed).
    staff = svc.create_user(owner_principal, "Aditi", STAFF_PHONE, "staff")
    svc.set_pin(staff.user_id, STAFF_PIN)
    device = _make_device(registry_db, owner)
    for _ in range(_MAX_MANAGE_ATTEMPTS):
        svc.elevate_to_manage(
            device.device_id, {"user_id": staff.user_id, "pin": "0000", "purpose": "switch"}
        )
    assert device.device_id not in auth_module._manage_lockouts
