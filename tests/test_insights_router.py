"""
Unit/integration tests for the Insights router (app-first pivot, task 16.1).

Exercises ``app/api/insights_router.py`` end-to-end through a FastAPI TestClient
with the auth dependencies overridden so the router runs against an in-memory
tenant DB (Req 16.1-16.7):

- financial questions blocked for Staff before any data is read (16.6)
- reporting-period validation: missing / inverted → message, no values (16.3)
- financial aggregation over the inclusive period (16.2)
- order cost = Σ ingredient qty × recorded unit price (16.4)
- ingredients missing a unit price identified, cost not fully computed (16.5)
- tenant-scoped answers + unanswerable-question messaging (16.1, 16.7)
"""

from datetime import date, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api import deps
from app.api.deps import AuthedUser
from app.api.errors import register_error_handlers
from app.api.insights_router import router
from app.models import (
    Base,
    Customer,
    InventoryItem,
    Order,
    OrderItem,
    Recipe,
    RecipeComponent,
    Tenant,
)


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture(scope="function")
def db():
    """In-memory SQLite shared across threads (TestClient uses a worker thread)."""
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
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


@pytest.fixture(scope="function")
def tenant_id(db):
    t = Tenant(chat_id="insights_chat_001")
    db.add(t)
    db.commit()
    db.refresh(t)
    return t.tenant_id


def _owner(tenant_id) -> AuthedUser:
    return AuthedUser(user_id=uuid4(), tenant_id=tenant_id, role="owner", device_id=uuid4())


def _staff(tenant_id) -> AuthedUser:
    return AuthedUser(user_id=uuid4(), tenant_id=tenant_id, role="staff", device_id=uuid4())


def _client(db, user) -> TestClient:
    app = FastAPI()
    register_error_handlers(app)
    app.include_router(router)
    app.dependency_overrides[deps.get_current_user] = lambda: user
    app.dependency_overrides[deps.get_tenant_db_for_user] = lambda: db
    return TestClient(app, raise_server_exceptions=True)


# ── Test data builders ────────────────────────────────────────────────────────

def _make_customer(db, tenant_id):
    c = Customer(tenant_id=tenant_id, name="Alice", phone="9876543210")
    db.add(c)
    db.commit()
    db.refresh(c)
    return c


def _make_inventory(db, tenant_id, name, cost, category="ingredient"):
    item = InventoryItem(
        tenant_id=tenant_id,
        name=name,
        category=category,
        quantity=Decimal("100"),
        unit="kg",
        cost_per_unit=Decimal(str(cost)),
    )
    db.add(item)
    db.commit()
    db.refresh(item)
    return item


def _make_recipe(db, tenant_id, name, yield_per_batch, components):
    """components: list of (InventoryItem, qty, type)."""
    recipe = Recipe(tenant_id=tenant_id, name=name, yield_per_batch=yield_per_batch)
    db.add(recipe)
    db.commit()
    db.refresh(recipe)
    for inv, qty, ctype in components:
        db.add(
            RecipeComponent(
                recipe_id=recipe.recipe_id,
                item_id=inv.item_id,
                quantity=Decimal(str(qty)),
                type=ctype,
            )
        )
    db.commit()
    db.refresh(recipe)
    return recipe


def _make_order(db, tenant_id, customer, recipe, qty, price, delivery_date, status="delivered"):
    order = Order(
        tenant_id=tenant_id,
        customer_id=customer.customer_id,
        delivery_date=delivery_date,
        status=status,
    )
    db.add(order)
    db.commit()
    db.refresh(order)
    db.add(
        OrderItem(
            order_id=order.order_id,
            recipe_id=recipe.recipe_id if recipe else None,
            recipe_name=recipe.name if recipe else "Unknown",
            quantity=qty,
            selling_price=Decimal(str(price)),
        )
    )
    db.commit()
    db.refresh(order)
    return order


# ── 16.6 Staff blocked from financial questions ───────────────────────────────

def test_staff_financial_question_blocked_before_data_read(db, tenant_id):
    client = _client(db, _staff(tenant_id))
    resp = client.post(
        "/api/v1/insights/ask",
        json={
            "question": "What was my revenue last month?",
            "start_date": "2024-01-01",
            "end_date": "2024-01-31",
        },
    )
    assert resp.status_code == 403
    assert resp.json()["error"] == "forbidden"


def test_staff_order_cost_question_blocked(db, tenant_id):
    client = _client(db, _staff(tenant_id))
    resp = client.post(
        "/api/v1/insights/ask",
        json={"question": "What is the cost of this order?", "order_id": str(uuid4())},
    )
    assert resp.status_code == 403


def test_staff_non_financial_question_allowed(db, tenant_id):
    client = _client(db, _staff(tenant_id))
    resp = client.post(
        "/api/v1/insights/ask",
        json={"question": "How many recipes do I have?"},
    )
    # Non-financial questions are not blocked for Staff; this one is unanswerable
    # deterministically, so it returns a 200 with an unanswerable message.
    assert resp.status_code == 200
    assert resp.json()["answerable"] is False


# ── 16.3 Period validation ─────────────────────────────────────────────────────

def test_missing_period_returns_message_no_values(db, tenant_id):
    client = _client(db, _owner(tenant_id))
    resp = client.post(
        "/api/v1/insights/ask",
        json={"question": "What is my profit?"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["answerable"] is False
    assert body["revenue"] is None
    assert body["cost"] is None
    assert body["profit"] is None
    assert "period" in body["answer"].lower()


def test_inverted_period_returns_message_no_values(db, tenant_id):
    client = _client(db, _owner(tenant_id))
    resp = client.post(
        "/api/v1/insights/ask",
        json={
            "question": "What is my revenue?",
            "start_date": "2024-02-01",
            "end_date": "2024-01-01",
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["answerable"] is False
    assert body["revenue"] is None
    assert body["profit"] is None
    assert "invalid" in body["answer"].lower()


# ── 16.2 Financial aggregation over the inclusive period ───────────────────────

def test_financial_aggregation_inclusive_period(db, tenant_id):
    customer = _make_customer(db, tenant_id)
    flour = _make_inventory(db, tenant_id, "Flour", "10")  # ₹10/unit
    # yield 2 units; 1 unit flour per batch → unit cost = 10*1/2 = 5 per unit
    recipe = _make_recipe(db, tenant_id, "Cake", 2, [(flour, 1, "ingredient")])

    start = date(2024, 1, 1)
    end = date(2024, 1, 31)
    # In-period delivered order: qty 4 @ ₹100 → revenue 400; cost 5*4 = 20
    _make_order(db, tenant_id, customer, recipe, 4, "100", date(2024, 1, 15))
    # Boundary date (inclusive) order: qty 1 @ ₹50 → revenue 50; cost 5
    _make_order(db, tenant_id, customer, recipe, 1, "50", end)
    # Out-of-period order (excluded)
    _make_order(db, tenant_id, customer, recipe, 10, "100", date(2024, 2, 5))

    client = _client(db, _owner(tenant_id))
    resp = client.post(
        "/api/v1/insights/ask",
        json={
            "question": "What was my profit and revenue?",
            "start_date": start.isoformat(),
            "end_date": end.isoformat(),
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["answerable"] is True
    assert Decimal(body["revenue"]) == Decimal("450")
    assert Decimal(body["cost"]) == Decimal("25")
    assert Decimal(body["profit"]) == Decimal("425")


def test_financial_aggregation_excludes_non_delivered(db, tenant_id):
    customer = _make_customer(db, tenant_id)
    flour = _make_inventory(db, tenant_id, "Flour", "10")
    recipe = _make_recipe(db, tenant_id, "Cake", 2, [(flour, 1, "ingredient")])

    start = date(2024, 1, 1)
    end = date(2024, 1, 31)
    _make_order(db, tenant_id, customer, recipe, 4, "100", date(2024, 1, 15), status="pending")

    client = _client(db, _owner(tenant_id))
    resp = client.post(
        "/api/v1/insights/ask",
        json={"question": "revenue", "start_date": start.isoformat(), "end_date": end.isoformat()},
    )
    body = resp.json()
    assert Decimal(body["revenue"]) == Decimal("0")


# ── 16.4 Order cost ────────────────────────────────────────────────────────────

def test_order_cost_sum_of_ingredient_qty_times_unit_price(db, tenant_id):
    customer = _make_customer(db, tenant_id)
    flour = _make_inventory(db, tenant_id, "Flour", "10")   # ₹10/unit
    sugar = _make_inventory(db, tenant_id, "Sugar", "20")   # ₹20/unit
    # yield 2; flour 2/batch, sugar 1/batch
    recipe = _make_recipe(
        db, tenant_id, "Cake", 2, [(flour, 2, "ingredient"), (sugar, 1, "ingredient")]
    )
    # order qty 4 → flour qty = 2/2*4 = 4 → 4*10=40 ; sugar = 1/2*4=2 → 2*20=40
    order = _make_order(db, tenant_id, customer, recipe, 4, "100", date(2024, 1, 10))

    client = _client(db, _owner(tenant_id))
    resp = client.post(
        "/api/v1/insights/ask",
        json={"question": "What is the cost of this order?", "order_id": str(order.order_id)},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["answerable"] is True
    assert Decimal(body["order_cost"]) == Decimal("80")


def test_order_cost_missing_order_is_unanswerable(db, tenant_id):
    client = _client(db, _owner(tenant_id))
    resp = client.post(
        "/api/v1/insights/ask",
        json={"question": "cost of order", "order_id": str(uuid4())},
    )
    assert resp.status_code == 200
    assert resp.json()["answerable"] is False


# ── 16.5 Ingredients missing a unit price ──────────────────────────────────────

def test_order_cost_reports_ingredients_missing_unit_price(db, tenant_id):
    customer = _make_customer(db, tenant_id)
    flour = _make_inventory(db, tenant_id, "Flour", "10")
    mystery = _make_inventory(db, tenant_id, "SecretSpice", "0")  # no recorded price
    recipe = _make_recipe(
        db, tenant_id, "Cake", 2, [(flour, 2, "ingredient"), (mystery, 1, "ingredient")]
    )
    order = _make_order(db, tenant_id, customer, recipe, 4, "100", date(2024, 1, 10))

    client = _client(db, _owner(tenant_id))
    resp = client.post(
        "/api/v1/insights/ask",
        json={"question": "order cost", "order_id": str(order.order_id)},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["answerable"] is False
    assert body["order_cost"] is None
    assert "SecretSpice" in body["missing_unit_price_ingredients"]
    assert "SecretSpice" in body["answer"]


# ── 16.1 Tenant scoping ────────────────────────────────────────────────────────

def test_order_cost_scoped_to_tenant(db, tenant_id):
    """An order in another tenant is invisible — reported as unanswerable."""
    customer = _make_customer(db, tenant_id)
    flour = _make_inventory(db, tenant_id, "Flour", "10")
    recipe = _make_recipe(db, tenant_id, "Cake", 2, [(flour, 1, "ingredient")])
    order = _make_order(db, tenant_id, customer, recipe, 1, "100", date(2024, 1, 10))

    other_tenant = uuid4()
    client = _client(db, _owner(other_tenant))
    resp = client.post(
        "/api/v1/insights/ask",
        json={"question": "cost of order", "order_id": str(order.order_id)},
    )
    assert resp.status_code == 200
    assert resp.json()["answerable"] is False


# ── 16.7 Unanswerable question ─────────────────────────────────────────────────

def test_unanswerable_question_returns_message_no_values(db, tenant_id):
    client = _client(db, _owner(tenant_id))
    resp = client.post(
        "/api/v1/insights/ask",
        json={"question": "What is the weather today?"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["answerable"] is False
    assert body["revenue"] is None
    assert body["cost"] is None
    assert body["profit"] is None
    assert body["order_cost"] is None
