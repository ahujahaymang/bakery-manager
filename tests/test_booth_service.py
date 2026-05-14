"""
Tests for BoothService — session lifecycle, items, checkout, reporting.
"""

from decimal import Decimal
from uuid import UUID

import pytest
from sqlalchemy.orm import Session

from app.booth.booth_service import BoothService, BoothItemInput
from app.models import Order, OrderItem, Payment, Product, ProductVariant, Tenant


# ── Helpers ────────────────────────────────────────────────────────────────────

def make_product(db: Session, tenant_id: UUID, name: str, price: Decimal) -> ProductVariant:
    """Create a product with one variant and return the variant."""
    product = Product(tenant_id=tenant_id, name=name, category="Test")
    db.add(product)
    db.flush()
    variant = ProductVariant(
        product_id=product.product_id,
        size_label="standard",
        price=price,
    )
    db.add(variant)
    db.commit()
    db.refresh(variant)
    return variant


# ── Session lifecycle ──────────────────────────────────────────────────────────

def test_start_session(db, tenant_id):
    svc = BoothService(db, tenant_id)
    session = svc.start_session("Pune Food Fest")
    assert session.name == "Pune Food Fest"
    assert session.ended_at is None
    assert session.tenant_id == tenant_id


def test_start_session_requires_name(db, tenant_id):
    svc = BoothService(db, tenant_id)
    with pytest.raises(ValueError, match="required"):
        svc.start_session("  ")


def test_only_one_active_session(db, tenant_id):
    svc = BoothService(db, tenant_id)
    svc.start_session("Session 1")
    with pytest.raises(ValueError, match="already active"):
        svc.start_session("Session 2")


def test_end_session(db, tenant_id):
    svc = BoothService(db, tenant_id)
    session = svc.start_session("Test Event")
    closed = svc.end_session(session.session_id)
    assert closed.ended_at is not None


def test_end_already_closed_session(db, tenant_id):
    svc = BoothService(db, tenant_id)
    session = svc.start_session("Test Event")
    svc.end_session(session.session_id)
    with pytest.raises(ValueError, match="already closed"):
        svc.end_session(session.session_id)


def test_get_active_session_none(db, tenant_id):
    svc = BoothService(db, tenant_id)
    assert svc.get_active_session() is None


def test_get_active_session_returns_open(db, tenant_id):
    svc = BoothService(db, tenant_id)
    created = svc.start_session("Active")
    active = svc.get_active_session()
    assert active is not None
    assert active.session_id == created.session_id


def test_list_sessions(db, tenant_id):
    svc = BoothService(db, tenant_id)
    s1 = svc.start_session("First")
    svc.end_session(s1.session_id)
    svc.start_session("Second")
    sessions = svc.list_sessions()
    assert len(sessions) == 2
    # Newest first
    assert sessions[0].name == "Second"


# ── Session items ──────────────────────────────────────────────────────────────

def test_add_item(db, tenant_id):
    variant = make_product(db, tenant_id, "Brownie", Decimal("400"))
    svc = BoothService(db, tenant_id)
    session = svc.start_session("Event")
    item = svc.add_item(session.session_id, variant.variant_id, Decimal("350"), stock_qty=20)
    assert item.booth_price == Decimal("350")
    assert item.stock_qty == 20
    assert item.sold_qty == 0


def test_add_item_unlimited_stock(db, tenant_id):
    variant = make_product(db, tenant_id, "Cookie", Decimal("50"))
    svc = BoothService(db, tenant_id)
    session = svc.start_session("Event")
    item = svc.add_item(session.session_id, variant.variant_id, Decimal("50"), stock_qty=None)
    assert item.stock_qty is None


def test_add_duplicate_item_raises(db, tenant_id):
    variant = make_product(db, tenant_id, "Cake", Decimal("800"))
    svc = BoothService(db, tenant_id)
    session = svc.start_session("Event")
    svc.add_item(session.session_id, variant.variant_id, Decimal("800"), stock_qty=10)
    with pytest.raises(ValueError, match="already in this session"):
        svc.add_item(session.session_id, variant.variant_id, Decimal("800"), stock_qty=5)


def test_remove_item(db, tenant_id):
    variant = make_product(db, tenant_id, "Muffin", Decimal("60"))
    svc = BoothService(db, tenant_id)
    session = svc.start_session("Event")
    svc.add_item(session.session_id, variant.variant_id, Decimal("60"), stock_qty=None)
    svc.remove_item(session.session_id, variant.variant_id)
    items = svc.list_items(session.session_id)
    assert len(items) == 0


def test_list_items_sorted(db, tenant_id):
    v1 = make_product(db, tenant_id, "Zebra Cake", Decimal("500"))
    v2 = make_product(db, tenant_id, "Apple Tart", Decimal("300"))
    svc = BoothService(db, tenant_id)
    session = svc.start_session("Event")
    svc.add_item(session.session_id, v1.variant_id, Decimal("500"), None)
    svc.add_item(session.session_id, v2.variant_id, Decimal("300"), None)
    items = svc.list_items(session.session_id)
    assert items[0].product_name == "Apple Tart"
    assert items[1].product_name == "Zebra Cake"


# ── Checkout ───────────────────────────────────────────────────────────────────

def test_checkout_cash_creates_order(db, tenant_id):
    variant = make_product(db, tenant_id, "Brownie", Decimal("400"))
    svc = BoothService(db, tenant_id)
    session = svc.start_session("Event")
    svc.add_item(session.session_id, variant.variant_id, Decimal("400"), stock_qty=10)

    result = svc.checkout(
        session_id=session.session_id,
        cart=[BoothItemInput(variant_id=variant.variant_id, quantity=2)],
        payment_method="cash",
    )

    assert result.total_amount == Decimal("800")
    assert result.payment_method == "cash"
    assert len(result.items) == 1
    assert result.items[0].quantity == 2


def test_checkout_sets_booth_session_id(db, tenant_id):
    variant = make_product(db, tenant_id, "Cookie", Decimal("50"))
    svc = BoothService(db, tenant_id)
    session = svc.start_session("Event")
    svc.add_item(session.session_id, variant.variant_id, Decimal("50"), None)

    result = svc.checkout(
        session_id=session.session_id,
        cart=[BoothItemInput(variant_id=variant.variant_id, quantity=1)],
        payment_method="cash",
    )

    order = db.query(Order).filter(Order.order_id == result.order_id).first()
    assert order is not None
    assert order.booth_session_id == session.session_id
    assert order.status == "delivered"


def test_checkout_uses_booth_price(db, tenant_id):
    variant = make_product(db, tenant_id, "Cake", Decimal("800"))  # catalog price
    svc = BoothService(db, tenant_id)
    session = svc.start_session("Event")
    svc.add_item(session.session_id, variant.variant_id, Decimal("750"), None)  # booth price

    result = svc.checkout(
        session_id=session.session_id,
        cart=[BoothItemInput(variant_id=variant.variant_id, quantity=1)],
        payment_method="cash",
    )

    assert result.total_amount == Decimal("750")
    oi = db.query(OrderItem).filter(OrderItem.order_id == result.order_id).first()
    assert oi.selling_price == Decimal("750")


def test_checkout_increments_sold_qty(db, tenant_id):
    variant = make_product(db, tenant_id, "Muffin", Decimal("60"))
    svc = BoothService(db, tenant_id)
    session = svc.start_session("Event")
    svc.add_item(session.session_id, variant.variant_id, Decimal("60"), stock_qty=10)

    svc.checkout(
        session_id=session.session_id,
        cart=[BoothItemInput(variant_id=variant.variant_id, quantity=3)],
        payment_method="cash",
    )

    items = svc.list_items(session.session_id)
    assert items[0].sold_qty == 3
    assert items[0].remaining == 7


def test_checkout_stock_exceeded_raises(db, tenant_id):
    variant = make_product(db, tenant_id, "Tart", Decimal("200"))
    svc = BoothService(db, tenant_id)
    session = svc.start_session("Event")
    svc.add_item(session.session_id, variant.variant_id, Decimal("200"), stock_qty=2)

    with pytest.raises(ValueError, match="Not enough stock"):
        svc.checkout(
            session_id=session.session_id,
            cart=[BoothItemInput(variant_id=variant.variant_id, quantity=5)],
            payment_method="cash",
        )


def test_checkout_razorpay_payment_pending(db, tenant_id):
    variant = make_product(db, tenant_id, "Cake", Decimal("800"))
    svc = BoothService(db, tenant_id)
    session = svc.start_session("Event")
    svc.add_item(session.session_id, variant.variant_id, Decimal("800"), None)

    result = svc.checkout(
        session_id=session.session_id,
        cart=[BoothItemInput(variant_id=variant.variant_id, quantity=1)],
        payment_method="razorpay",
    )

    payment = db.query(Payment).filter(Payment.order_id == result.order_id).first()
    assert payment.status == "pending"


def test_checkout_cash_payment_completed(db, tenant_id):
    variant = make_product(db, tenant_id, "Cookie", Decimal("100"))
    svc = BoothService(db, tenant_id)
    session = svc.start_session("Event")
    svc.add_item(session.session_id, variant.variant_id, Decimal("100"), None)

    result = svc.checkout(
        session_id=session.session_id,
        cart=[BoothItemInput(variant_id=variant.variant_id, quantity=1)],
        payment_method="cash",
    )

    payment = db.query(Payment).filter(Payment.order_id == result.order_id).first()
    assert payment.status == "completed"


def test_checkout_empty_cart_raises(db, tenant_id):
    svc = BoothService(db, tenant_id)
    session = svc.start_session("Event")
    with pytest.raises(ValueError, match="empty"):
        svc.checkout(session_id=session.session_id, cart=[], payment_method="cash")


def test_checkout_closed_session_raises(db, tenant_id):
    variant = make_product(db, tenant_id, "Cake", Decimal("500"))
    svc = BoothService(db, tenant_id)
    session = svc.start_session("Event")
    svc.add_item(session.session_id, variant.variant_id, Decimal("500"), None)
    svc.end_session(session.session_id)

    with pytest.raises(ValueError, match="ended"):
        svc.checkout(
            session_id=session.session_id,
            cart=[BoothItemInput(variant_id=variant.variant_id, quantity=1)],
            payment_method="cash",
        )


# ── Razorpay confirmation ──────────────────────────────────────────────────────

def test_confirm_razorpay_payment(db, tenant_id):
    variant = make_product(db, tenant_id, "Cake", Decimal("800"))
    svc = BoothService(db, tenant_id)
    session = svc.start_session("Event")
    svc.add_item(session.session_id, variant.variant_id, Decimal("800"), None)

    result = svc.checkout(
        session_id=session.session_id,
        cart=[BoothItemInput(variant_id=variant.variant_id, quantity=1)],
        payment_method="razorpay",
    )

    svc.confirm_razorpay_payment("pay_abc123", result.order_id)

    payment = db.query(Payment).filter(Payment.order_id == result.order_id).first()
    assert payment.status == "completed"
    assert payment.razorpay_payment_id == "pay_abc123"


def test_get_payment_status(db, tenant_id):
    variant = make_product(db, tenant_id, "Cookie", Decimal("50"))
    svc = BoothService(db, tenant_id)
    session = svc.start_session("Event")
    svc.add_item(session.session_id, variant.variant_id, Decimal("50"), None)

    result = svc.checkout(
        session_id=session.session_id,
        cart=[BoothItemInput(variant_id=variant.variant_id, quantity=1)],
        payment_method="cash",
    )

    assert svc.get_payment_status(result.order_id) == "completed"


# ── Reporting ──────────────────────────────────────────────────────────────────

def test_session_summary(db, tenant_id):
    v1 = make_product(db, tenant_id, "Brownie", Decimal("400"))
    v2 = make_product(db, tenant_id, "Cookie", Decimal("100"))
    svc = BoothService(db, tenant_id)
    session = svc.start_session("Food Fest")
    svc.add_item(session.session_id, v1.variant_id, Decimal("400"), None)
    svc.add_item(session.session_id, v2.variant_id, Decimal("100"), None)

    svc.checkout(session.session_id,
                 [BoothItemInput(v1.variant_id, 2), BoothItemInput(v2.variant_id, 3)],
                 "cash")
    svc.checkout(session.session_id,
                 [BoothItemInput(v1.variant_id, 1)],
                 "cash")

    summary = svc.get_session_summary(session.session_id)
    assert summary.total_orders == 2
    assert summary.total_revenue == Decimal("1500")  # 3×400 + 3×100
    assert summary.items_sold == 6
    assert len(summary.top_products) > 0
    # Brownie should be top (₹1200 revenue)
    assert summary.top_products[0].product_name == "Brownie — standard"
