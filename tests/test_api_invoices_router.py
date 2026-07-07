"""
Unit tests for the invoices domain router (task 12.1).

Exercises `app/api/invoices_router.py` by invoking the route handler directly
with a fixed `AuthedUser` principal and the shared in-memory SQLite session
(`db` fixture). (The environment has no `httpx`, so we call the handler function
rather than going through an ASGI transport.)

Covers Requirement 13:
- generate_invoice → PDF with business/customer/items/subtotal/total in the
  tenant currency (Req 13.1, 13.3)
- optional GST breakdown when a tax rate is configured, none otherwise
  (Req 13.2, 13.7)
- order-not-found rejection (Req 13.5)
- invoice generated for any delivery date — past, today, or future (Req 13.4)

The registry lookup used to resolve the tenant business name/currency is
monkeypatched to reuse the same in-memory session.
"""

import base64
from datetime import date, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest

import app.models  # noqa: F401  (register all tables on Base)
from app.models import Customer, Order, OrderItem
from app.api import errors, invoices_router
from app.api.deps import AuthedUser
from app.api.invoices_router import InvoiceGenerateRequest, generate_invoice


@pytest.fixture()
def env(db, tenant, monkeypatch):
    """Seed a customer + order with one line item; return handles for tests.

    The registry lookup used by the router to resolve business name/currency is
    patched to reuse the same in-memory session.
    """
    tenant.business_name = "Sweet Treats"
    tenant.country = "india"
    db.commit()

    def _fake_registry_db():
        yield db

    monkeypatch.setattr("app.database.get_registry_db", _fake_registry_db)

    owner = AuthedUser(uuid4(), tenant.tenant_id, "owner", uuid4())
    staff = AuthedUser(uuid4(), tenant.tenant_id, "staff", uuid4())

    return {
        "db": db,
        "tenant": tenant,
        "tenant_id": tenant.tenant_id,
        "owner": owner,
        "staff": staff,
    }


def _make_order(db, tenant_id, delivery_date):
    """Create a customer + order with a single line item; return the order id."""
    customer = Customer(tenant_id=tenant_id, name="Alice Baker", phone="11112222")
    db.add(customer)
    db.flush()

    order = Order(
        tenant_id=tenant_id,
        customer_id=customer.customer_id,
        delivery_date=delivery_date,
        status="pending",
    )
    db.add(order)
    db.flush()

    db.add(
        OrderItem(
            order_id=order.order_id,
            recipe_name="Chocolate Cake",
            quantity=2,
            selling_price=Decimal("500"),
            customization_charge=Decimal("0"),
        )
    )
    db.commit()
    return order.order_id


def _req(order_id, tax_rate=Decimal("0"), tax_label=None):
    return InvoiceGenerateRequest(
        order_id=str(order_id), tax_rate=tax_rate, tax_label=tax_label
    )


# ── PDF generation & currency (Req 13.1, 13.3) ─────────────────────────────────

def test_generate_invoice_returns_base64_pdf(env):
    order_id = _make_order(env["db"], env["tenant_id"], date.today())
    body = generate_invoice(_req(order_id), user=env["owner"], db=env["db"])

    assert body["content_type"] == "application/pdf"
    pdf = base64.b64decode(body["data"])
    assert pdf.startswith(b"%PDF")

    inv = body["invoice"]
    assert inv["currency"] == "Rs."  # tenant currency for india (Req 13.3)
    assert inv["subtotal"] == Decimal("1000")  # 2 * 500
    assert len(inv["items"]) == 1
    assert inv["items"][0]["description"] == "Chocolate Cake"
    assert inv["items"][0]["quantity"] == 2
    assert body["filename"].endswith(".pdf")


def test_staff_can_generate_invoice(env):
    order_id = _make_order(env["db"], env["tenant_id"], date.today())
    body = generate_invoice(_req(order_id), user=env["staff"], db=env["db"])
    assert body["content_type"] == "application/pdf"


# ── GST breakdown (Req 13.2, 13.7) ──────────────────────────────────────────────

def test_gst_breakdown_included_when_tax_rate_configured(env):
    order_id = _make_order(env["db"], env["tenant_id"], date.today())
    body = generate_invoice(
        _req(order_id, tax_rate=Decimal("5")), user=env["owner"], db=env["db"]
    )
    inv = body["invoice"]
    assert inv["tax_rate"] == Decimal("5")
    assert inv["tax_amount"] == Decimal("50.00")  # 1000 * 5%
    assert inv["tax_label"] == "GST (5%)"


def test_custom_tax_label_used_when_provided(env):
    order_id = _make_order(env["db"], env["tenant_id"], date.today())
    body = generate_invoice(
        _req(order_id, tax_rate=Decimal("18"), tax_label="IGST (18%)"),
        user=env["owner"],
        db=env["db"],
    )
    assert body["invoice"]["tax_label"] == "IGST (18%)"


def test_no_gst_breakdown_when_tax_rate_absent(env):
    order_id = _make_order(env["db"], env["tenant_id"], date.today())
    body = generate_invoice(_req(order_id), user=env["owner"], db=env["db"])
    inv = body["invoice"]
    assert inv["tax_rate"] == Decimal("0")
    assert inv["tax_amount"] == Decimal("0")
    assert inv["tax_label"] == ""


# ── Order-not-found rejection (Req 13.5) ────────────────────────────────────────

def test_unknown_order_rejected_404(env):
    with pytest.raises(errors.NotFoundError):
        generate_invoice(_req(uuid4()), user=env["owner"], db=env["db"])


def test_invalid_order_id_rejected(env):
    with pytest.raises(errors.ValidationError):
        generate_invoice(
            InvoiceGenerateRequest(order_id="not-a-uuid"),
            user=env["owner"],
            db=env["db"],
        )


# ── Delivery date rules (Req 13.4) ──────────────────────────────────────────────
# An invoice is generated for an order regardless of its delivery date so that
# advance/made-to-order orders can be invoiced at order time.

def test_delivery_date_today_accepted(env):
    order_id = _make_order(env["db"], env["tenant_id"], date.today())
    body = generate_invoice(_req(order_id), user=env["owner"], db=env["db"])
    assert body["invoice"]["delivery_date"] == date.today()


def test_delivery_date_in_past_accepted(env):
    past = date.today() - timedelta(days=3)
    order_id = _make_order(env["db"], env["tenant_id"], past)
    body = generate_invoice(_req(order_id), user=env["owner"], db=env["db"])
    assert body["invoice"]["delivery_date"] == past


def test_future_delivery_date_accepted(env):
    future = date.today() + timedelta(days=1)
    order_id = _make_order(env["db"], env["tenant_id"], future)
    body = generate_invoice(_req(order_id), user=env["owner"], db=env["db"])
    assert body["invoice"]["delivery_date"] == future
