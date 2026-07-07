"""
Tests for the Sell router (`app/api/sell_router.py`, task 7.1).

Exercises the router handlers over the unchanged ``BoothService`` by calling the
endpoint functions directly with an injected ``AuthedUser`` and a tenant DB
session (the same pattern the FastAPI dependencies would provide). This avoids a
live HTTP transport dependency while still validating the full router logic:

- get_sell_session → active session + items with unit prices (Req 8.1)
- checkout         → order+payment, attribution, idempotency, empty-cart,
                     failure atomicity (Req 7.1, 7.4, 7.5, 8.3, 8.4, 8.7, 8.8, 18.5)
- get_receipt      → printable receipt data (Req 8.5)
- get_invoice      → downloadable PDF invoice (Req 8.5)

Registry + tenant DB share one in-memory SQLite session; the registry lookup in
the invoice/receipt context is monkeypatched to bind to it.
"""

import base64
from datetime import datetime
from decimal import Decimal
from uuid import UUID, uuid4

import pytest

import app.models  # noqa: F401  (register all tables on Base)
from app.models import Order, Payment, Product, ProductVariant, SellIdempotency
from app.api import errors, sell_router
from app.api.deps import AuthedUser
from app.api.sell_router import (
    SellCheckoutItem,
    SellCheckoutRequest,
    checkout,
    get_invoice,
    get_receipt,
    get_sell_session,
)
from app.booth.booth_service import BoothService


@pytest.fixture()
def env(db, tenant, monkeypatch):
    """Seed a product/variant and an active session; return handles for tests.

    ``db``/``tenant`` come from the shared conftest fixtures. The registry lookup
    used by receipt/invoice is patched to reuse the same in-memory session.
    """
    product = Product(tenant_id=tenant.tenant_id, name="Brownie", category="Test")
    db.add(product)
    db.flush()
    variant = ProductVariant(
        product_id=product.product_id, size_label="standard", price=Decimal("400")
    )
    db.add(variant)
    db.commit()

    svc = BoothService(db, tenant.tenant_id)
    session = svc.start_session("Sell Event")
    svc.add_item(session.session_id, variant.variant_id, Decimal("400"), stock_qty=20)

    def _fake_registry_db():
        yield db

    monkeypatch.setattr("app.database.get_registry_db", _fake_registry_db)

    owner = AuthedUser(uuid4(), tenant.tenant_id, "owner", uuid4())
    staff = AuthedUser(uuid4(), tenant.tenant_id, "staff", uuid4())

    return {
        "db": db,
        "tenant_id": tenant.tenant_id,
        "variant_id": variant.variant_id,
        "session_id": session.session_id,
        "owner": owner,
        "staff": staff,
    }


def _item(env, qty=1):
    return SellCheckoutItem(variant_id=str(env["variant_id"]), quantity=qty)


def _req(env, qty=1, payment_method="cash", session_id=None):
    return SellCheckoutRequest(
        items=[_item(env, qty)],
        payment_method=payment_method,
        session_id=session_id,
    )


# ── get_sell_session (Req 8.1) ──────────────────────────────────────────────────

def test_session_lists_items_with_unit_price(env):
    result = get_sell_session(user=env["owner"], db=env["db"])
    session = result["session"]
    assert session is not None
    assert len(session["items"]) == 1
    item = session["items"][0]
    assert item["unit_price"] == 400.0
    assert item["variant_id"] == str(env["variant_id"])


# ── checkout (Req 7, 8.3–8.8) ───────────────────────────────────────────────────

def test_checkout_creates_order_and_payment_attributed(env):
    body = checkout(_req(env, qty=2), user=env["owner"], db=env["db"], idempotency_key=None)
    assert body["total_amount"] == 800.0
    assert body["payment_method"] == "Cash"
    # Req 7.1/7.4 — sale attributed to the acting user.
    assert body["created_by_user_id"] == str(env["owner"].user_id)

    db = env["db"]
    order_id = UUID(body["order_id"])
    order = db.query(Order).filter(Order.order_id == order_id).first()
    assert order.created_by_user_id == env["owner"].user_id
    # Exactly one order + one payment (Req 8.3).
    assert db.query(Order).count() == 1
    assert db.query(Payment).filter(Payment.order_id == order_id).count() == 1


def test_checkout_records_upi_payment_method(env):
    body = checkout(_req(env, payment_method="upi"), user=env["owner"], db=env["db"], idempotency_key=None)
    assert body["payment_method"] == "Upi"


def test_checkout_empty_cart_rejected_no_records(env):
    empty = SellCheckoutRequest(items=[], payment_method="cash")
    with pytest.raises(errors.ValidationError):
        checkout(empty, user=env["owner"], db=env["db"], idempotency_key=None)
    # Req 8.8 — nothing persisted.
    assert env["db"].query(Order).count() == 0
    assert env["db"].query(Payment).count() == 0


def test_checkout_failure_creates_no_records(env):
    # Stock is 20; requesting 999 makes BoothService raise → mapped error, nothing persisted (Req 8.7).
    with pytest.raises(errors.APIError):
        checkout(_req(env, qty=999), user=env["owner"], db=env["db"], idempotency_key=None)
    assert env["db"].query(Order).count() == 0
    assert env["db"].query(Payment).count() == 0


def test_staff_can_checkout_attributed_to_staff(env):
    body = checkout(_req(env), user=env["staff"], db=env["db"], idempotency_key=None)
    assert body["created_by_user_id"] == str(env["staff"].user_id)


def test_checkout_uses_active_session_when_id_omitted(env):
    body = checkout(_req(env, session_id=None), user=env["owner"], db=env["db"], idempotency_key=None)
    order = env["db"].query(Order).filter(Order.order_id == UUID(body["order_id"])).first()
    assert order.booth_session_id == env["session_id"]


# ── Idempotency (Req 8.3, 18.5) ─────────────────────────────────────────────────

def test_idempotent_checkout_returns_existing_order(env):
    first = checkout(_req(env), user=env["owner"], db=env["db"], idempotency_key="sale-key-1")
    assert first["idempotent_replay"] is False

    second = checkout(_req(env), user=env["owner"], db=env["db"], idempotency_key="sale-key-1")
    assert second["idempotent_replay"] is True

    # Same order returned; only one order created despite two requests.
    assert first["order_id"] == second["order_id"]
    assert env["db"].query(Order).count() == 1
    assert env["db"].query(SellIdempotency).count() == 1


def test_distinct_idempotency_keys_create_distinct_orders(env):
    r1 = checkout(_req(env), user=env["owner"], db=env["db"], idempotency_key="k1")
    r2 = checkout(_req(env), user=env["owner"], db=env["db"], idempotency_key="k2")
    assert r1["order_id"] != r2["order_id"]
    assert env["db"].query(Order).count() == 2


# ── Receipt & invoice (Req 8.5) ─────────────────────────────────────────────────

def test_receipt_returns_data(env):
    sale = checkout(_req(env, qty=2), user=env["owner"], db=env["db"], idempotency_key=None)
    body = get_receipt(sale["order_id"], user=env["owner"], db=env["db"])
    assert body["total_amount"] == 800.0
    assert len(body["items"]) == 1
    assert body["payment_method"] == "Cash"


def test_receipt_unknown_order_404(env):
    with pytest.raises(errors.NotFoundError):
        get_receipt(str(uuid4()), user=env["owner"], db=env["db"])


def test_invoice_returns_base64_pdf(env):
    sale = checkout(_req(env), user=env["owner"], db=env["db"], idempotency_key=None)
    body = get_invoice(sale["order_id"], tax_rate=0, user=env["owner"], db=env["db"])
    assert body["content_type"] == "application/pdf"
    pdf = base64.b64decode(body["data"])
    assert pdf.startswith(b"%PDF")


def test_invoice_unknown_order_404(env):
    with pytest.raises(errors.NotFoundError):
        get_invoice(str(uuid4()), tax_rate=0, user=env["owner"], db=env["db"])
