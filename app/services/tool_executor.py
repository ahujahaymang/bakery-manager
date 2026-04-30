"""
Tool Executor - maps LLM tool calls to service methods.

Each tool name maps to a service call. Results are returned as strings
for the LLM to interpret and present to the user.
"""

import logging
from datetime import date
from decimal import Decimal
from typing import Any, Dict
from uuid import UUID

from app.services.customer_service import CustomerService
from app.services.inventory_service import InventoryService
from app.services.recipe_service import RecipeService
from app.services.order_service import OrderCreate, OrderItemCreate, OrderService
from app.services.payment_service import PaymentService
from app.services.reporting_service import ReportingService
from app.helpers.order_finder import OrderFinder
from app.models import Payment

logger = logging.getLogger(__name__)


class ToolExecutor:
    """
    Executes tool calls from the LLM agent against the service layer.
    All methods return plain strings - the LLM formats the final response.
    """

    def __init__(self, db, tenant_id: UUID):
        self.db = db
        self.tenant_id = tenant_id

    async def execute(self, tool_name: str, args: Dict[str, Any]) -> str:
        handler = getattr(self, f"_tool_{tool_name}", None)
        if not handler:
            return f"Unknown tool: {tool_name}"
        try:
            return await handler(args)
        except ValueError as e:
            return f"Error: {str(e)}"
        except Exception as e:
            logger.error(f"Tool {tool_name} failed: {e}", exc_info=True)
            return f"Error: {str(e)}"

    # ── Customers ──────────────────────────────────────────────────────────

    async def _tool_create_customer(self, args):
        svc = CustomerService(self.db)
        c = svc.create_customer(self.tenant_id, args["name"], args["phone"])
        return f"Customer created: {c.name} ({c.phone})"

    async def _tool_get_customer(self, args):
        svc = CustomerService(self.db)
        customers = svc.get_customer(self.tenant_id, args["search"])
        if not customers:
            return f"No customer found matching '{args['search']}'"
        return "\n".join(f"{c.name} ({c.phone})" for c in customers)

    async def _tool_list_customers(self, args):
        svc = CustomerService(self.db)
        customers = svc.list_customers(self.tenant_id)
        if not customers:
            return "No customers found"
        return "\n".join(f"{c.name} - {c.phone}" for c in customers)

    # ── Inventory ──────────────────────────────────────────────────────────

    async def _tool_add_inventory(self, args):
        svc = InventoryService(self.db)
        item = svc.create_item(
            tenant_id=self.tenant_id,
            name=args["name"],
            category=args["category"],
            quantity=Decimal(str(args["quantity"])),
            unit=args["unit"],
            cost_per_unit=Decimal(str(args["cost_per_unit"]))
        )
        return f"Added: {item.name} ({item.category}) - {item.quantity} {item.unit} @ ₹{item.cost_per_unit}/{item.unit}"

    async def _tool_update_inventory(self, args):
        svc = InventoryService(self.db)
        updates = {}
        if "quantity" in args:
            updates["quantity"] = args["quantity"]
        if "cost_per_unit" in args:
            updates["cost_per_unit"] = args["cost_per_unit"]
        item = svc.update_item(self.tenant_id, args["name"], updates)
        return f"Updated: {item.name} - {item.quantity} {item.unit} @ ₹{item.cost_per_unit}/{item.unit}"

    async def _tool_check_stock(self, args):
        svc = InventoryService(self.db)
        item = svc.get_item(self.tenant_id, args["name"])
        if not item:
            return f"Item '{args['name']}' not found in inventory"
        return f"{item.name}: {item.quantity} {item.unit} @ ₹{item.cost_per_unit}/{item.unit}"

    async def _tool_list_inventory(self, args):
        svc = InventoryService(self.db)
        grouped = svc.list_items(self.tenant_id)
        category = args.get("category")

        lines = []
        for cat in (["ingredient", "packaging"] if not category else [category]):
            items = grouped.get(cat, [])
            if items:
                lines.append(f"{cat.title()}s:")
                for item in items:
                    lines.append(f"  {item.name}: {item.quantity} {item.unit} @ ₹{item.cost_per_unit}/{item.unit}")

        return "\n".join(lines) if lines else "No inventory items found"

    # ── Recipes ────────────────────────────────────────────────────────────

    async def _tool_create_recipe(self, args):
        svc = RecipeService(self.db)
        existing = svc.get_recipe(self.tenant_id, args["name"])
        if existing:
            return (
                f"ALREADY_EXISTS: A recipe named '{existing.name}' already exists "
                f"(yield: {existing.yield_per_batch} units/batch). "
                f"Ask the user: do they want to replace it, or save under a different name?"
            )
        recipe = svc.create_recipe(self.tenant_id, args["name"], args["yield_per_batch"])
        return f"Recipe created: {recipe.name} (yield: {recipe.yield_per_batch} units/batch)"

    async def _tool_replace_recipe(self, args):
        svc = RecipeService(self.db)
        try:
            svc.delete_recipe(self.tenant_id, args["name"])
        except ValueError:
            pass  # didn't exist, that's fine
        recipe = svc.create_recipe(self.tenant_id, args["name"], args["yield_per_batch"])
        return f"Recipe replaced: {recipe.name} (yield: {recipe.yield_per_batch} units/batch)"

    async def _tool_list_recipes(self, args):
        svc = RecipeService(self.db)
        recipes = svc.list_recipes(self.tenant_id)
        if not recipes:
            return "No recipes found"
        return "\n".join(f"{r.name} (yield: {r.yield_per_batch}/batch)" for r in recipes)

    async def _tool_get_recipe(self, args):
        svc = RecipeService(self.db)
        data = svc.get_recipe_with_components(self.tenant_id, args["name"])
        if not data:
            return f"Recipe '{args['name']}' not found"

        lines = [
            f"Recipe: {data['name']}",
            f"Yield: {data['yield_per_batch']} units/batch",
            "",
            "Ingredients:"
        ]
        for ing in data["ingredients"]:
            lines.append(f"  {ing['item_name']}: {ing['quantity']} {ing['unit']}")
        if not data["ingredients"]:
            lines.append("  (none)")

        lines.append("Packaging:")
        for pkg in data["packaging"]:
            lines.append(f"  {pkg['item_name']}: {pkg['quantity']} {pkg['unit']}")
        if not data["packaging"]:
            lines.append("  (none)")

        try:
            cost = svc.calculate_cost(self.tenant_id, data["name"])
            lines.append(f"\nCost per unit: ₹{cost.unit_cost:.2f}")
        except Exception:
            pass

        return "\n".join(lines)

    async def _tool_update_recipe(self, args):
        svc = RecipeService(self.db)
        recipe = svc.update_recipe(
            self.tenant_id,
            args["name"],
            new_name=args.get("new_name"),
            new_yield=args.get("new_yield")
        )
        return f"Recipe updated: {recipe.name} (yield: {recipe.yield_per_batch}/batch)"

    async def _tool_add_recipe_component(self, args):
        svc = RecipeService(self.db)
        svc.add_component(
            tenant_id=self.tenant_id,
            recipe_name=args["recipe_name"],
            item_name=args["item_name"],
            quantity=Decimal(str(args["quantity"])),
            component_type=args["component_type"]
        )
        return f"Added {args['item_name']} ({args['quantity']}) to {args['recipe_name']}"

    async def _tool_remove_recipe_component(self, args):
        svc = RecipeService(self.db)
        removed = svc.remove_component(self.tenant_id, args["recipe_name"], args["item_name"])
        if removed:
            return f"Removed {args['item_name']} from {args['recipe_name']}"
        return f"{args['item_name']} was not found in {args['recipe_name']}"

    async def _tool_update_recipe_component(self, args):
        svc = RecipeService(self.db)
        component = svc.update_component_quantity(
            self.tenant_id,
            args["recipe_name"],
            args["item_name"],
            Decimal(str(args["quantity"]))
        )
        return f"Updated {args['item_name']} in {args['recipe_name']} to {component.quantity}"

    async def _tool_delete_recipe(self, args):
        svc = RecipeService(self.db)
        svc.delete_recipe(self.tenant_id, args["name"])
        return f"Recipe '{args['name']}' deleted"

    async def _tool_calculate_recipe_cost(self, args):
        svc = RecipeService(self.db)
        cost = svc.calculate_cost(self.tenant_id, args["recipe_name"])
        return (
            f"Recipe: {cost.recipe_name}\n"
            f"Ingredient cost: ₹{cost.ingredient_cost:.2f}\n"
            f"Packaging cost: ₹{cost.packaging_cost:.2f}\n"
            f"Cost per unit: ₹{cost.unit_cost:.2f}"
        )

    # ── Orders ─────────────────────────────────────────────────────────────

    async def _tool_create_order(self, args):
        svc = OrderService(self.db)
        items = [
            OrderItemCreate(
                recipe_name=i["recipe_name"],
                quantity=int(i["quantity"]),
                selling_price=Decimal(str(i["selling_price"]))
            )
            for i in args["items"]
        ]
        order = svc.create_order(
            self.tenant_id,
            OrderCreate(
                customer_identifier=args["customer_identifier"],
                delivery_date=date.fromisoformat(args["delivery_date"]),
                items=items
            )
        )
        lines = [
            f"Order created for delivery on {order.delivery_date}",
            f"Status: {order.status}",
            "Items:"
        ]
        for item in items:
            lines.append(f"  {item.recipe_name} x{item.quantity} @ ₹{item.selling_price}")

        if hasattr(order, "_missing_recipes") and order._missing_recipes:
            lines.append(f"\nNote: These recipes don't exist yet: {', '.join(order._missing_recipes)}")

        return "\n".join(lines)

    async def _tool_cancel_order(self, args):
        finder = OrderFinder(self.db)
        customer_identifier = args["customer_identifier"]
        delivery_date_str = args.get("delivery_date")

        from app.services.customer_service import CustomerService
        cust_svc = CustomerService(self.db)
        customers = cust_svc.get_customer(self.tenant_id, customer_identifier)

        if not customers:
            return f"No customer found matching '{customer_identifier}'"
        if len(customers) > 1:
            names = ", ".join(f"{c.name} ({c.phone})" for c in customers)
            return f"Multiple customers match: {names}. Please be more specific."

        customer = customers[0]
        if delivery_date_str:
            order = finder.find_by_customer_and_date(
                self.tenant_id, customer.customer_id, date.fromisoformat(delivery_date_str)
            )
        else:
            orders = finder.find_by_customer(self.tenant_id, customer.customer_id, status_filter="pending")
            if not orders:
                return f"No pending orders found for {customer.name}"
            if len(orders) > 1:
                lines = [f"Multiple pending orders for {customer.name}:"]
                for o in orders:
                    lines.append(f"  Delivery: {o.delivery_date}")
                lines.append("Please specify the delivery date.")
                return "\n".join(lines)
            order = orders[0]

        if not order:
            return "Order not found"
        if order.status == "cancelled":
            return "Order is already cancelled"
        if order.status == "delivered":
            return "Cannot cancel a delivered order"

        order.status = "cancelled"
        self.db.commit()
        return f"Order for {customer.name} on {order.delivery_date} marked as cancelled"

    async def _tool_delete_order(self, args):
        from app.models import OrderItem
        finder = OrderFinder(self.db)
        customer_identifier = args["customer_identifier"]
        delivery_date_str = args.get("delivery_date")

        from app.services.customer_service import CustomerService
        cust_svc = CustomerService(self.db)
        customers = cust_svc.get_customer(self.tenant_id, customer_identifier)

        if not customers:
            return f"No customer found matching '{customer_identifier}'"
        if len(customers) > 1:
            names = ", ".join(f"{c.name} ({c.phone})" for c in customers)
            return f"Multiple customers match: {names}. Please be more specific."

        customer = customers[0]
        if delivery_date_str:
            order = finder.find_by_customer_and_date(
                self.tenant_id, customer.customer_id, date.fromisoformat(delivery_date_str)
            )
        else:
            orders = finder.find_by_customer(self.tenant_id, customer.customer_id)
            if not orders:
                return f"No orders found for {customer.name}"
            if len(orders) > 1:
                lines = [f"Multiple orders for {customer.name}:"]
                for o in orders:
                    lines.append(f"  Delivery: {o.delivery_date} ({o.status})")
                lines.append("Please specify the delivery date.")
                return "\n".join(lines)
            order = orders[0]

        if not order:
            return "Order not found"

        payments = self.db.query(Payment).filter(Payment.order_id == order.order_id).all()
        if payments:
            total = sum(p.amount for p in payments)
            return f"Cannot delete: order has ₹{total} in payments recorded"

        self.db.query(OrderItem).filter(OrderItem.order_id == order.order_id).delete()
        self.db.delete(order)
        self.db.commit()
        return f"Order for {customer.name} on {order.delivery_date} permanently deleted"

    async def _tool_mark_delivered(self, args):
        svc = OrderService(self.db)
        order = svc.mark_delivered(self.tenant_id, UUID(args["order_id"]))
        return f"Order {order.order_id} marked as delivered"

    async def _tool_upcoming_orders(self, args):
        svc = OrderService(self.db)
        orders = svc.get_upcoming_orders(self.tenant_id)
        filter_type = args.get("filter")

        if filter_type:
            filtered = []
            for o in orders:
                payments = self.db.query(Payment).filter(Payment.order_id == o["order_id"]).all()
                paid = sum(p.amount for p in payments)
                total = Decimal(str(o["total_price"]))
                is_paid = paid >= total
                is_delivered = o.get("status") == "delivered"
                if filter_type == "paid" and is_paid:
                    filtered.append(o)
                elif filter_type == "unpaid" and not is_paid:
                    filtered.append(o)
                elif filter_type == "delivered" and is_delivered:
                    filtered.append(o)
                elif filter_type == "pending" and not is_delivered:
                    filtered.append(o)
            orders = filtered

        if not orders:
            return f"No{' ' + filter_type if filter_type else ''} orders found"

        lines = []
        for o in orders:
            lines.append(f"{o['customer_name']} ({o['customer_phone']}) - {o['delivery_date']}")
            for item in o["items"]:
                lines.append(f"  {item['recipe_name']} x{item['quantity']} @ ₹{item['selling_price']}")
            lines.append(f"  Total: ₹{o['total_price']}")
            lines.append("")
        return "\n".join(lines)

    async def _tool_unpaid_orders(self, args):
        svc = OrderService(self.db)
        orders = svc.get_unpaid_orders(self.tenant_id)
        if not orders:
            return "All orders are paid!"
        lines = []
        for o in orders:
            lines.append(f"{o['customer_name']}: total ₹{o['total_amount']}, paid ₹{o['amount_paid']}, due ₹{o['amount_due']}")
        return "\n".join(lines)

    # ── Payments ───────────────────────────────────────────────────────────

    async def _tool_record_payment(self, args):
        from app.services.payment_service import PaymentService
        from dataclasses import dataclass

        @dataclass
        class PaymentCreate:
            order_identifier: str
            amount: Decimal
            method: str

        svc = PaymentService(self.db)
        payment = svc.record_payment(
            self.tenant_id,
            PaymentCreate(
                order_identifier=args["order_identifier"],
                amount=Decimal(str(args["amount"])),
                method=args["method"]
            )
        )
        return f"Payment recorded: ₹{payment.amount} via {payment.method}"

    async def _tool_payment_history(self, args):
        from app.services.payment_service import PaymentService
        svc = PaymentService(self.db)
        start = date.fromisoformat(args["start_date"]) if args.get("start_date") else None
        end = date.fromisoformat(args["end_date"]) if args.get("end_date") else None
        payments = svc.get_payment_history(self.tenant_id, start, end)
        if not payments:
            return "No payment history found"
        lines = []
        for p in payments:
            lines.append(f"{p['customer_name']}: ₹{p['amount']} via {p['method']} on {p['payment_date']}")
        return "\n".join(lines)

    # ── Reporting ──────────────────────────────────────────────────────────

    async def _tool_weekly_profit(self, args):
        svc = ReportingService(self.db)
        report = svc.calculate_weekly_profit(self.tenant_id)
        return (
            f"Week: {report.week_start} to {report.week_end}\n"
            f"Revenue: ₹{report.total_revenue:.2f}\n"
            f"Ingredient cost: ₹{report.total_ingredient_cost:.2f}\n"
            f"Packaging cost: ₹{report.total_packaging_cost:.2f}\n"
            f"Gross profit: ₹{report.gross_profit:.2f}"
        )
