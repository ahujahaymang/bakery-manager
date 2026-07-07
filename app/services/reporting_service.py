"""
Reporting Service for generating business reports.

This service handles profit calculations and other reporting operations
with proper tenant isolation.
"""

from typing import Dict, Any
from uuid import UUID
from decimal import Decimal
from datetime import date, datetime, timedelta
from dataclasses import dataclass
from sqlalchemy.orm import Session

from app.models import Order, OrderItem, Recipe, RecipeComponent, InventoryItem


@dataclass
class ProfitReport:
    """
    Profit report over an arbitrary inclusive date range.

    ``period_start``/``period_end`` are the actual (inclusive) bounds the report
    was computed over. The four totals use the same summation as the weekly
    report — revenue from delivered orders and per-unit recipe costs.
    """
    period_start: date
    period_end: date
    total_revenue: Decimal
    total_ingredient_cost: Decimal
    total_packaging_cost: Decimal
    gross_profit: Decimal


@dataclass
class WeeklyProfitReport:
    """Data class for weekly profit report (current Monday–Sunday week)."""
    week_start: date
    week_end: date
    total_revenue: Decimal
    total_ingredient_cost: Decimal
    total_packaging_cost: Decimal
    gross_profit: Decimal


class ReportingService:
    """
    Service for generating business reports.
    
    Handles profit calculations and other reporting operations with tenant
    isolation.
    """
    
    def __init__(self, db: Session):
        """
        Initialize ReportingService with database session.
        
        Args:
            db: SQLAlchemy database session
        """
        self.db = db

    def calculate_profit(
        self,
        tenant_id: UUID,
        start_date: date,
        end_date: date,
    ) -> ProfitReport:
        """
        Calculate gross profit over an inclusive ``[start_date, end_date]`` range.

        Retrieves all delivered orders whose ``delivery_date`` falls within the
        inclusive range, then computes total revenue, ingredient cost, packaging
        cost, and gross profit.

        Args:
            tenant_id: UUID of the tenant
            start_date: inclusive start of the reporting period
            end_date: inclusive end of the reporting period

        Returns:
            ProfitReport: report carrying the actual period bounds plus the four
            totals (revenue, ingredient cost, packaging cost, gross profit).

        The math mirrors :meth:`calculate_weekly_profit`:
            - total_revenue = Σ order_item.quantity × order_item.selling_price
            - ingredient/packaging cost via recipe components ÷ yield × quantity
            - gross_profit = total_revenue − ingredient − packaging
        """
        # Retrieve all delivered orders within the inclusive range for tenant
        orders = self.db.query(Order).filter(
            Order.tenant_id == tenant_id,
            Order.status == "delivered",
            Order.delivery_date >= start_date,
            Order.delivery_date <= end_date
        ).all()

        # Initialize totals
        total_revenue = Decimal('0')
        total_ingredient_cost = Decimal('0')
        total_packaging_cost = Decimal('0')

        # Process each order
        for order in orders:
            # Get all order items for this order
            order_items = self.db.query(OrderItem).filter(
                OrderItem.order_id == order.order_id
            ).all()

            for order_item in order_items:
                # Calculate revenue for this order item
                item_revenue = order_item.quantity * order_item.selling_price
                total_revenue += item_revenue

                # Get recipe for this order item
                recipe = self.db.query(Recipe).filter(
                    Recipe.recipe_id == order_item.recipe_id
                ).first()

                if recipe:
                    # Get all recipe components
                    recipe_components = self.db.query(RecipeComponent, InventoryItem).join(
                        InventoryItem,
                        RecipeComponent.item_id == InventoryItem.item_id
                    ).filter(
                        RecipeComponent.recipe_id == recipe.recipe_id
                    ).all()

                    # Calculate costs for this recipe
                    recipe_ingredient_cost = Decimal('0')
                    recipe_packaging_cost = Decimal('0')

                    for component, inventory_item in recipe_components:
                        # Calculate cost for this component
                        component_cost = component.quantity * inventory_item.cost_per_unit

                        # Add to appropriate category
                        if component.type == "ingredient":
                            recipe_ingredient_cost += component_cost
                        elif component.type == "packaging":
                            recipe_packaging_cost += component_cost

                    # Calculate unit costs (cost per single item from the batch)
                    unit_ingredient_cost = recipe_ingredient_cost / recipe.yield_per_batch
                    unit_packaging_cost = recipe_packaging_cost / recipe.yield_per_batch

                    # Multiply by quantity ordered
                    total_ingredient_cost += unit_ingredient_cost * order_item.quantity
                    total_packaging_cost += unit_packaging_cost * order_item.quantity

        # Calculate gross profit
        gross_profit = total_revenue - total_ingredient_cost - total_packaging_cost

        return ProfitReport(
            period_start=start_date,
            period_end=end_date,
            total_revenue=total_revenue,
            total_ingredient_cost=total_ingredient_cost,
            total_packaging_cost=total_packaging_cost,
            gross_profit=gross_profit
        )

    def calculate_weekly_profit(
        self,
        tenant_id: UUID
    ) -> WeeklyProfitReport:
        """
        Calculate gross profit for the current week.
        
        Calculates the date range for the current week (Monday to Sunday) and
        delegates to :meth:`calculate_profit` for the actual summation, so the
        behaviour stays identical to a range report over that week.
        
        Args:
            tenant_id: UUID of the tenant
        
        Returns:
            WeeklyProfitReport: Report with revenue, costs, and profit
        
        Requirements:
            - 18.1: Calculate date range for current week (Monday to Sunday)
            - 18.2: Retrieve all Orders with status "delivered" and delivery_date within the week
            - 18.3: Calculate total_revenue as SUM(order_item.quantity × order_item.selling_price)
            - 18.4: Calculate total_ingredient_cost by summing recipe ingredient costs
            - 18.5: Calculate total_packaging_cost by summing recipe packaging costs
            - 18.6: Calculate gross_profit = total_revenue - total_ingredient_cost - total_packaging_cost
            - 18.7: Display all four values
            - 18.8: NOT use LLM for profit calculations
        """
        # Calculate current week date range (Monday to Sunday)
        today = date.today()
        # Get the Monday of the current week (weekday() returns 0 for Monday)
        days_since_monday = today.weekday()
        week_start = today - timedelta(days=days_since_monday)
        week_end = week_start + timedelta(days=6)  # Sunday

        # Delegate the summation to the generic range calculation
        report = self.calculate_profit(tenant_id, week_start, week_end)

        return WeeklyProfitReport(
            week_start=week_start,
            week_end=week_end,
            total_revenue=report.total_revenue,
            total_ingredient_cost=report.total_ingredient_cost,
            total_packaging_cost=report.total_packaging_cost,
            gross_profit=report.gross_profit
        )
