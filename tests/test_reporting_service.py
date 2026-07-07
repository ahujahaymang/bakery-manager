"""
Tests for ReportingService profit calculations.

Covers:
- ``calculate_profit`` over a custom multi-week range (revenue / ingredient
  cost / packaging cost / gross profit match the documented formula).
- Inclusive boundary handling (delivery_date == start_date or == end_date is
  counted; one day outside the range is not).
- ``calculate_weekly_profit`` still returns the current Mon–Sun week and matches
  a direct range report over that same week (refactor is behaviour-preserving).
"""

from decimal import Decimal
from datetime import date, timedelta

from app.models import (
    Customer,
    InventoryItem,
    Recipe,
    RecipeComponent,
    Order,
    OrderItem,
)
from app.services.reporting_service import ReportingService


# ── Fixture builders ─────────────────────────────────────────────────────────
#
# Recipe "Brownie" — yield 10 per batch:
#   ingredient "Flour": 1000 g/batch @ ₹0.05/g  → ₹50/batch → ₹5/unit
#   packaging  "Box":   10 pcs/batch @ ₹2/pc    → ₹20/batch → ₹2/unit
# Selling price: ₹100/unit.
UNIT_REVENUE = Decimal("100")
UNIT_INGREDIENT_COST = Decimal("5")   # 50 / 10
UNIT_PACKAGING_COST = Decimal("2")    # 20 / 10


def _setup_recipe(db, tenant_id):
    flour = InventoryItem(
        tenant_id=tenant_id, name="Flour", category="ingredient",
        quantity=Decimal("0"), unit="g", cost_per_unit=Decimal("0.05"),
    )
    box = InventoryItem(
        tenant_id=tenant_id, name="Box", category="packaging",
        quantity=Decimal("0"), unit="pcs", cost_per_unit=Decimal("2"),
    )
    db.add_all([flour, box])
    db.flush()

    recipe = Recipe(tenant_id=tenant_id, name="Brownie", yield_per_batch=10)
    db.add(recipe)
    db.flush()

    db.add_all([
        RecipeComponent(
            recipe_id=recipe.recipe_id, item_id=flour.item_id,
            quantity=Decimal("1000"), type="ingredient",
        ),
        RecipeComponent(
            recipe_id=recipe.recipe_id, item_id=box.item_id,
            quantity=Decimal("10"), type="packaging",
        ),
    ])

    customer = Customer(tenant_id=tenant_id, name="Alice", phone="9876543210")
    db.add(customer)
    db.flush()

    db.commit()
    return recipe, customer


def _add_delivered_order(db, tenant_id, customer, recipe, delivery_date, quantity, status="delivered"):
    order = Order(
        tenant_id=tenant_id,
        customer_id=customer.customer_id,
        delivery_date=delivery_date,
        status=status,
    )
    db.add(order)
    db.flush()
    db.add(OrderItem(
        order_id=order.order_id,
        recipe_id=recipe.recipe_id,
        recipe_name=recipe.name,
        quantity=quantity,
        selling_price=UNIT_REVENUE,
    ))
    db.commit()
    return order


# ── calculate_profit over a custom multi-week range ───────────────────────────

def test_calculate_profit_custom_multiweek_range(db, tenant_id):
    recipe, customer = _setup_recipe(db, tenant_id)

    start = date(2024, 1, 1)
    end = date(2024, 1, 31)

    # Two delivered orders in different weeks, both inside the range.
    _add_delivered_order(db, tenant_id, customer, recipe, date(2024, 1, 3), quantity=2)
    _add_delivered_order(db, tenant_id, customer, recipe, date(2024, 1, 20), quantity=3)

    report = ReportingService(db).calculate_profit(tenant_id, start, end)

    total_qty = Decimal("5")
    assert report.period_start == start
    assert report.period_end == end
    assert report.total_revenue == UNIT_REVENUE * total_qty            # 500
    assert report.total_ingredient_cost == UNIT_INGREDIENT_COST * total_qty  # 25
    assert report.total_packaging_cost == UNIT_PACKAGING_COST * total_qty     # 10
    assert report.gross_profit == (
        report.total_revenue
        - report.total_ingredient_cost
        - report.total_packaging_cost
    )  # 465


def test_calculate_profit_excludes_non_delivered(db, tenant_id):
    recipe, customer = _setup_recipe(db, tenant_id)
    start = date(2024, 1, 1)
    end = date(2024, 1, 31)

    _add_delivered_order(db, tenant_id, customer, recipe, date(2024, 1, 10), quantity=2)
    # Pending order in range must not contribute.
    _add_delivered_order(db, tenant_id, customer, recipe, date(2024, 1, 11), quantity=5, status="pending")

    report = ReportingService(db).calculate_profit(tenant_id, start, end)
    assert report.total_revenue == UNIT_REVENUE * Decimal("2")


# ── Inclusive boundary handling ───────────────────────────────────────────────

def test_calculate_profit_boundary_inclusive(db, tenant_id):
    recipe, customer = _setup_recipe(db, tenant_id)
    start = date(2024, 3, 1)
    end = date(2024, 3, 31)

    # On the exact boundaries → included.
    _add_delivered_order(db, tenant_id, customer, recipe, start, quantity=1)
    _add_delivered_order(db, tenant_id, customer, recipe, end, quantity=1)
    # Just outside the range → excluded.
    _add_delivered_order(db, tenant_id, customer, recipe, start - timedelta(days=1), quantity=4)
    _add_delivered_order(db, tenant_id, customer, recipe, end + timedelta(days=1), quantity=4)

    report = ReportingService(db).calculate_profit(tenant_id, start, end)

    # Only the two boundary orders (qty 1 each) count.
    assert report.total_revenue == UNIT_REVENUE * Decimal("2")
    assert report.total_ingredient_cost == UNIT_INGREDIENT_COST * Decimal("2")
    assert report.total_packaging_cost == UNIT_PACKAGING_COST * Decimal("2")


def test_calculate_profit_empty_range(db, tenant_id):
    _setup_recipe(db, tenant_id)
    report = ReportingService(db).calculate_profit(
        tenant_id, date(2024, 6, 1), date(2024, 6, 30)
    )
    assert report.total_revenue == Decimal("0")
    assert report.gross_profit == Decimal("0")


# ── calculate_weekly_profit still works and delegates to calculate_profit ─────

def test_calculate_weekly_profit_matches_current_week(db, tenant_id):
    recipe, customer = _setup_recipe(db, tenant_id)

    today = date.today()
    week_start = today - timedelta(days=today.weekday())
    week_end = week_start + timedelta(days=6)

    # Delivered order inside this week.
    _add_delivered_order(db, tenant_id, customer, recipe, today, quantity=2)
    # Delivered order well before this week → excluded from the weekly report.
    _add_delivered_order(db, tenant_id, customer, recipe, week_start - timedelta(days=10), quantity=7)

    svc = ReportingService(db)
    weekly = svc.calculate_weekly_profit(tenant_id)

    assert weekly.week_start == week_start
    assert weekly.week_end == week_end
    assert weekly.total_revenue == UNIT_REVENUE * Decimal("2")

    # Weekly report equals a direct range report over the same Mon–Sun window.
    ranged = svc.calculate_profit(tenant_id, week_start, week_end)
    assert weekly.total_revenue == ranged.total_revenue
    assert weekly.total_ingredient_cost == ranged.total_ingredient_cost
    assert weekly.total_packaging_cost == ranged.total_packaging_cost
    assert weekly.gross_profit == ranged.gross_profit
