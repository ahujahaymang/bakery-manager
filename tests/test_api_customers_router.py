"""
Unit tests for the customers domain router (task 11.1).

Exercises `app/api/customers_router.py` by invoking the route handlers directly
with a fixed `AuthedUser` principal and the shared in-memory SQLite session
(`db` fixture). (The environment has no `httpx`, so we call the handlers
directly rather than through a FastAPI TestClient; the handlers contain all the
router logic under test.) Covers:

- create with valid name/phone (Req 12.1)
- name/phone validation errors (Req 12.4)
- per-tenant phone-uniqueness conflict identifying the duplicate phone (Req 12.3)
- search by name/phone capped at 50 results, empty-term behaviour (Req 12.2)
"""

from uuid import uuid4

import pytest
from pydantic import ValidationError as PydanticValidationError

from app.api import customers_router
from app.api.deps import AuthedUser
from app.api.errors import ConflictError, ValidationError
from app.api.schemas import CustomerCreateRequest
from app.services.customer_service import CustomerService


@pytest.fixture()
def principal(tenant_id):
    """A Staff principal bound to the test tenant (Customers is Owner+Staff)."""
    return AuthedUser(user_id=uuid4(), tenant_id=tenant_id, role="staff", device_id=uuid4())


def _create(body_dict, principal, db):
    return customers_router.create_customer(CustomerCreateRequest(**body_dict), principal, db)


# ── create: happy path (Req 12.1) ─────────────────────────────────────────────

def test_create_customer_succeeds(principal, db):
    out = _create({"name": "Alice", "phone": "9876543210"}, principal, db)
    assert out["name"] == "Alice"
    assert out["phone"] == "9876543210"
    assert "customer_id" in out


def test_create_customer_accepts_optional_address(principal, db):
    out = _create({"name": "Bob", "phone": "12345678", "address": "12 Baker St"}, principal, db)
    assert out["address"] == "12 Baker St"


# ── create: validation (Req 12.4) ─────────────────────────────────────────────

# These pass the request-model shape (8-20 chars) but violate the router's
# 8-15 *digits* rule: too many digits, non-digit characters, or a leading '+'.
@pytest.mark.parametrize("phone", ["1234567890123456", "98765abcd", "+919876543210"])
def test_create_rejects_invalid_phone(principal, db, phone):
    with pytest.raises(ValidationError) as exc:
        _create({"name": "Alice", "phone": phone}, principal, db)
    assert exc.value.field == "phone"


def test_invalid_phone_persists_no_record(principal, db, tenant_id):
    # Digit-invalid phones that clear the request-model length constraint but
    # are rejected by the router; confirm nothing is written (Req 12.4).
    with pytest.raises(ValidationError):
        _create({"name": "Alice", "phone": "abcdefgh"}, principal, db)
    with pytest.raises(ValidationError):
        _create({"name": "Alice", "phone": "1234567890123456"}, principal, db)
    assert CustomerService(db).list_customers(tenant_id) == []


def test_request_model_rejects_empty_name():
    # Empty name is rejected by the request-model constraint (name 1-100).
    with pytest.raises(PydanticValidationError):
        CustomerCreateRequest(name="", phone="9876543210")


def test_request_model_rejects_overlong_name():
    with pytest.raises(PydanticValidationError):
        CustomerCreateRequest(name="x" * 101, phone="9876543210")


# ── create: per-tenant phone uniqueness (Req 12.3) ────────────────────────────

def test_duplicate_phone_conflict_identifies_phone(principal, db, tenant_id):
    CustomerService(db).create_customer(tenant_id, "Alice", "9876543210")

    with pytest.raises(ConflictError) as exc:
        _create({"name": "Alice2", "phone": "9876543210"}, principal, db)
    assert exc.value.field == "phone"
    assert "9876543210" in exc.value.detail


def test_duplicate_phone_leaves_existing_record_unchanged(principal, db, tenant_id):
    original = CustomerService(db).create_customer(tenant_id, "Alice", "9876543210")

    with pytest.raises(ConflictError):
        _create({"name": "Alice2", "phone": "9876543210"}, principal, db)

    still = CustomerService(db).get_customer_by_phone(tenant_id, "9876543210")
    assert still.customer_id == original.customer_id
    assert still.name == "Alice"


# ── search (Req 12.2) ─────────────────────────────────────────────────────────

def test_search_by_name_partial(principal, db, tenant_id):
    CustomerService(db).create_customer(tenant_id, "Alice Baker", "11111111")
    CustomerService(db).create_customer(tenant_id, "Bob", "22222222")

    out = customers_router.search_customers("ali", principal, db)
    assert [c["name"] for c in out] == ["Alice Baker"]


def test_search_by_phone_exact(principal, db, tenant_id):
    CustomerService(db).create_customer(tenant_id, "Alice", "11111111")
    CustomerService(db).create_customer(tenant_id, "Bob", "22222222")

    out = customers_router.search_customers("22222222", principal, db)
    assert len(out) == 1
    assert out[0]["name"] == "Bob"


def test_search_empty_term_returns_empty_list(principal, db, tenant_id):
    CustomerService(db).create_customer(tenant_id, "Alice", "11111111")

    assert customers_router.search_customers("", principal, db) == []


def test_search_capped_at_50(principal, db, tenant_id):
    for i in range(60):
        CustomerService(db).create_customer(tenant_id, f"Cust {i:03d}", f"9{i:09d}")

    out = customers_router.search_customers("cust", principal, db)
    assert len(out) == 50
