"""
Tests for the Sell product-setup surface (always-on session sync + Add item).

Covers the app-first additions to ``app/api/sell_router.py``:

- ``GET /session`` auto-starts an "always-on" Sell session and syncs the catalog
  into it with unlimited stock (``remaining is None``) — Req 8.1.
- ``POST /products`` (Owner-only) creates a product which then appears in the
  next ``GET /session`` grid.
- A duplicate product name surfaces as a mapped conflict error.

Follows ``tests/test_sell_router.py``'s direct-call style: the route callables
are invoked with an injected ``AuthedUser`` and the shared in-memory tenant DB
session (the same objects the FastAPI dependencies would provide). The registry
lookup used by receipt/invoice context is not exercised here.
"""

from decimal import Decimal
from uuid import uuid4

import pytest

import app.models  # noqa: F401  (register all tables on Base)
from app.api import errors
from app.api.deps import AuthedUser
from app.api.sell_router import (
    SellProductRequest,
    add_sell_product,
    get_sell_session,
)
from app.services.product_service import ProductService, VariantInput


@pytest.fixture()
def owner(tenant) -> AuthedUser:
    return AuthedUser(uuid4(), tenant.tenant_id, "owner", uuid4())


@pytest.fixture()
def staff(tenant) -> AuthedUser:
    return AuthedUser(uuid4(), tenant.tenant_id, "staff", uuid4())


# ── GET /session auto-start + catalog sync (Req 8.1) ────────────────────────────

def test_session_autostarts_when_none_active(db, owner):
    """With no session and no products, GET /session starts an empty session."""
    result = get_sell_session(user=owner, db=db)
    session = result["session"]
    assert session is not None
    assert session["name"] == "Sell"
    assert session["mode"] == "regular"
    assert session["items"] == []


def test_session_syncs_catalog_with_unlimited_stock(db, tenant, owner):
    """Existing catalog variants appear in the session with unlimited stock."""
    ProductService(db).create_product(
        tenant.tenant_id,
        "Brownie",
        [VariantInput(size_label="standard", price=Decimal("400"))],
        category="Bakes",
    )

    result = get_sell_session(user=owner, db=db)
    items = result["session"]["items"]
    assert len(items) == 1
    item = items[0]
    assert item["product_name"] == "Brownie"
    assert item["unit_price"] == 400.0
    # Unlimited stock → stock_qty and remaining are both None (Req 8.1).
    assert item["stock_qty"] is None
    assert item["remaining"] is None


def test_session_sync_is_idempotent_across_calls(db, tenant, owner):
    """Repeated GET /session calls don't duplicate session items."""
    ProductService(db).create_product(
        tenant.tenant_id,
        "Cookie",
        [VariantInput(size_label="standard", price=Decimal("50"))],
    )
    first = get_sell_session(user=owner, db=db)["session"]["items"]
    second = get_sell_session(user=owner, db=db)["session"]["items"]
    assert len(first) == 1
    assert len(second) == 1


# ── POST /products (Owner "Add item") ───────────────────────────────────────────

def test_add_product_returns_created_product(db, owner):
    body = SellProductRequest(name="Muffin", category="Bakes", price=Decimal("120"))
    created = add_sell_product(body=body, user=owner, db=db)
    assert created["name"] == "Muffin"
    assert created["category"] == "Bakes"
    assert len(created["variants"]) == 1
    assert created["variants"][0]["size_label"] == "standard"
    assert created["variants"][0]["price"] == 120.0


def test_added_product_appears_in_session(db, owner):
    body = SellProductRequest(name="Tart", price=Decimal("200"))
    add_sell_product(body=body, user=owner, db=db)

    result = get_sell_session(user=owner, db=db)
    names = [it["product_name"] for it in result["session"]["items"]]
    assert "Tart" in names
    tart = next(it for it in result["session"]["items"] if it["product_name"] == "Tart")
    assert tart["unit_price"] == 200.0
    assert tart["remaining"] is None


def test_add_duplicate_product_name_conflicts(db, owner):
    body = SellProductRequest(name="Eclair", price=Decimal("90"))
    add_sell_product(body=body, user=owner, db=db)

    dup = SellProductRequest(name="Eclair", price=Decimal("95"))
    with pytest.raises(errors.APIError) as exc:
        add_sell_product(body=dup, user=owner, db=db)
    # Duplicate name maps to a 409 conflict.
    assert exc.value.status_code == 409
