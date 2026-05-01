"""Tests for OrderService."""

import pytest
from decimal import Decimal
from datetime import date, timedelta
from uuid import uuid4

from app.services.order_service import OrderService, OrderCreate, OrderItemCreate
from app.services.customer_service import CustomerService
from app.services.recipe_service import RecipeService


def _make_customer(db, tenant_id, name="Alice", phone="9876543210"):
    return CustomerService(db).create_customer(tenant_id, name, phone)


def _make_recipe(db, tenant_id, name="Brownie", yield_per_batch=12):
    return RecipeService(db).create_recipe(tenant_id, name, yield_per_batch)


def _tomorrow():
    return date.today() + timedelta(days=1)


def _order_data(customer_phone, recipe_name, delivery_date=None):
    return OrderCreate(
        customer_identifier=customer_phone,
        delivery_date=delivery_date or _tomorrow(),
        items=[OrderItemCreate(recipe_name=recipe_name, quantity=2, selling_price=Decimal("150"))],
    )


class TestCreateOrder:
    def test_creates_order_successfully(self, db, tenant_id):
        customer = _make_customer(db, tenant_id)
        _make_recipe(db, tenant_id)
        svc = OrderService(db)
        order = svc.create_order(tenant_id, _order_data(customer.phone, "Brownie"))
        assert order.status == "pending"
        assert order.customer_id == customer.customer_id

    def test_resolves_customer_by_name(self, db, tenant_id):
        customer = _make_customer(db, tenant_id, name="Alice")
        _make_recipe(db, tenant_id)
        svc = OrderService(db)
        order = svc.create_order(tenant_id, _order_data("Alice", "Brownie"))
        assert order.customer_id == customer.customer_id

    def test_allows_unknown_recipe(self, db, tenant_id):
        """Orders can be created even if the recipe doesn't exist yet."""
        _make_customer(db, tenant_id)
        svc = OrderService(db)
        order = svc.create_order(tenant_id, _order_data("9876543210", "UnknownRecipe"))
        assert order is not None
        assert "UnknownRecipe" in (order._missing_recipes or [])

    def test_rejects_past_delivery_date(self, db, tenant_id):
        _make_customer(db, tenant_id)
        svc = OrderService(db)
        with pytest.raises(ValueError, match="cannot be in the past"):
            svc.create_order(
                tenant_id,
                _order_data("9876543210", "Brownie", date.today() - timedelta(days=1)),
            )

    def test_rejects_unknown_customer(self, db, tenant_id):
        svc = OrderService(db)
        with pytest.raises(ValueError, match="No customer found"):
            svc.create_order(tenant_id, _order_data("NoOne", "Brownie"))

    def test_rejects_empty_items(self, db, tenant_id):
        _make_customer(db, tenant_id)
        svc = OrderService(db)
        with pytest.raises(ValueError, match="At least one"):
            svc.create_order(
                tenant_id,
                OrderCreate(customer_identifier="9876543210", delivery_date=_tomorrow(), items=[]),
            )

    def test_rejects_zero_selling_price(self, db, tenant_id):
        _make_customer(db, tenant_id)
        svc = OrderService(db)
        with pytest.raises(ValueError, match="positive"):
            svc.create_order(
                tenant_id,
                OrderCreate(
                    customer_identifier="9876543210",
                    delivery_date=_tomorrow(),
                    items=[OrderItemCreate(recipe_name="Brownie", quantity=1, selling_price=Decimal("0"))],
                ),
            )

    def test_tenant_isolation(self, db, tenant_id):
        from app.models import Tenant
        other = Tenant(chat_id="other_chat")
        db.add(other)
        db.commit()

        _make_customer(db, tenant_id)
        svc = OrderService(db)
        with pytest.raises(ValueError, match="No customer found"):
            svc.create_order(other.tenant_id, _order_data("9876543210", "Brownie"))


class TestMarkDelivered:
    def test_marks_order_delivered(self, db, tenant_id):
        _make_customer(db, tenant_id)
        svc = OrderService(db)
        order = svc.create_order(tenant_id, _order_data("9876543210", "Brownie"))
        updated = svc.mark_delivered(tenant_id, order.order_id)
        assert updated.status == "delivered"

    def test_raises_for_missing_order(self, db, tenant_id):
        svc = OrderService(db)
        with pytest.raises(ValueError, match="Order not found"):
            svc.mark_delivered(tenant_id, uuid4())

    def test_tenant_isolation(self, db, tenant_id):
        from app.models import Tenant
        other = Tenant(chat_id="other_chat")
        db.add(other)
        db.commit()

        _make_customer(db, tenant_id)
        svc = OrderService(db)
        order = svc.create_order(tenant_id, _order_data("9876543210", "Brownie"))
        with pytest.raises(ValueError, match="Order not found"):
            svc.mark_delivered(other.tenant_id, order.order_id)
