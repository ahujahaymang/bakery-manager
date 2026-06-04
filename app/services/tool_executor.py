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
from app.services.order_finder import OrderFinder
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
        c = svc.create_customer(
            self.tenant_id,
            args["name"],
            args["phone"],
            address=args.get("address")
        )
        result = f"Customer created: {c.name} ({c.phone})"
        if c.address:
            result += f"\nDefault address: {c.address}"
        return result

    async def _tool_get_customer(self, args):
        svc = CustomerService(self.db)
        search = args.get("search", "").strip()
        if not search:
            # No search term — list all customers
            customers = svc.list_customers(self.tenant_id)
            if not customers:
                return "No customers found"
            return "\n".join(f"{c.name} - {c.phone}" for c in customers)
        customers = svc.get_customer(self.tenant_id, search)
        if not customers:
            return f"No customer found matching '{search}'"
        lines = []
        for c in customers:
            line = f"{c.name} ({c.phone})"
            if c.address:
                line += f" — default address: {c.address}"
            lines.append(line)
        return "\n".join(lines)

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
        result = f"Recipe created: {recipe.name} (yield: {recipe.yield_per_batch} units/batch)"

        # Suggest recipe link if matching unlinked products exist
        from app.services.product_service import ProductService
        prod_svc = ProductService(self.db)
        matches = prod_svc.find_matching_products_for_recipe(self.tenant_id, recipe.name)
        if matches:
            if len(matches) == 1:
                result += (
                    f"\n\nCHOOSE:💡 Found a matching product — link recipe *{recipe.name}* to it?\n"
                    f"Yes, link to {matches[0].name}\n"
                    f"No, skip"
                )
            else:
                options = "\n".join(p.name for p in matches[:5])
                result += f"\n\nCHOOSE:💡 Which product does this recipe belong to?\n{options}\nNone of these"
        return result

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
        """
        Add a component to a recipe.

        If the inventory item doesn't exist yet, auto-create it as a placeholder
        with cost 0 so the recipe can be saved immediately. The user can update
        costs later via add_inventory or update_inventory.
        """
        inv_svc = InventoryService(self.db)
        recipe_svc = RecipeService(self.db)

        item_name = args["item_name"]
        component_type = args["component_type"]

        # Auto-create inventory placeholder if item doesn't exist
        item = inv_svc.get_item(self.tenant_id, item_name)
        created_placeholder = False
        if not item:
            # Infer a sensible default unit from component type
            default_unit = "g" if component_type == "ingredient" else "pcs"
            inv_svc.create_item(
                tenant_id=self.tenant_id,
                name=item_name,
                category=component_type,
                quantity=Decimal("0"),
                unit=default_unit,
                cost_per_unit=Decimal("0"),
            )
            created_placeholder = True

        recipe_svc.add_component(
            tenant_id=self.tenant_id,
            recipe_name=args["recipe_name"],
            item_name=item_name,
            quantity=Decimal(str(args["quantity"])),
            component_type=component_type,
        )

        msg = f"Added {item_name} ({args['quantity']}) to {args['recipe_name']}"
        if created_placeholder:
            msg += " [inventory placeholder created — update cost later]"
        return msg

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
                selling_price=Decimal(str(i["selling_price"])),
                customization_charge=Decimal(str(i.get("customization_charge", 0))),
                customization_note=i.get("customization_note"),
            )
            for i in args["items"]
        ]
        order = svc.create_order(
            self.tenant_id,
            OrderCreate(
                customer_identifier=args["customer_identifier"],
                delivery_date=date.fromisoformat(args["delivery_date"]),
                items=items,
                delivery_address=args.get("delivery_address")
            )
        )
        lines = [
            f"Order created for delivery on {order.delivery_date}",
            f"Status: {order.status}",
        ]
        if order.delivery_address:
            lines.append(f"Delivery address: {order.delivery_address}")
        lines.append("Items:")
        for item in items:
            line = f"  {item.recipe_name} x{item.quantity} @ ₹{item.selling_price}"
            if item.customization_charge and item.customization_charge > 0:
                line += f" + ₹{item.customization_charge} customization"
                if item.customization_note:
                    line += f" ({item.customization_note})"
            lines.append(line)

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

    # ── Instagram ──────────────────────────────────────────────────────────

    async def _tool_connect_instagram(self, args):
        """Instagram integration is coming soon."""
        return "INSTAGRAM_CONNECT_URL:NOT_CONFIGURED"

    # ── Order template ─────────────────────────────────────────────────────

    async def _tool_set_order_template(self, args):
        from app.services.tenant_service import TenantService
        from app.database import get_registry_db
        reg_db = next(get_registry_db())
        try:
            TenantService(reg_db).set_order_template(self.tenant_id, args["template"])
        finally:
            reg_db.close()
        return (
            "✅ Order template saved! From now on, when you paste a filled-in order "
            "in this format I'll create it straight away without asking for each field."
        )

    async def _tool_get_order_template(self, args):
        from app.services.tenant_service import TenantService
        from app.database import get_registry_db
        reg_db = next(get_registry_db())
        try:
            template = TenantService(reg_db).get_order_template(self.tenant_id)
        finally:
            reg_db.close()
        if not template:
            return "No order template saved yet."
        return f"Current order template:\n\n{template}"

    # ── Booth ───────────────────────────────────────────────────────────────

    async def _tool_record_expense(self, args):
        """Record any business expense."""
        from app.models import PurchaseExpense
        from datetime import date as date_type
        import uuid as uuid_mod

        expense_date_str = args.get("expense_date", "")
        try:
            expense_date = date_type.fromisoformat(expense_date_str)
        except (ValueError, TypeError):
            expense_date = date_type.today()

        category = args.get("category", "other")
        if category not in PurchaseExpense.CATEGORIES:
            category = "other"

        is_capital = str(args.get("is_capital", False)).lower() == "true"

        expense = PurchaseExpense(
            expense_id=uuid_mod.uuid4(),
            tenant_id=self.tenant_id,
            amount=Decimal(str(args["amount"])),
            vendor_name=args.get("vendor_name"),
            expense_date=expense_date,
            category=category,
            is_capital="true" if is_capital else "false",
            description=args.get("description"),
            notes=args.get("notes"),
        )
        self.db.add(expense)
        self.db.commit()

        parts = [f"✅ Expense recorded: ₹{expense.amount:.2f}"]
        parts.append(f"Category: {category}")
        if expense.description:
            parts.append(f"Item: {expense.description}")
        if expense.vendor_name:
            parts.append(f"From: {expense.vendor_name}")
        parts.append(f"Date: {expense_date}")
        if is_capital:
            parts.append("📦 Logged as capital asset")
        return "\n".join(parts)

    async def _tool_list_expenses(self, args):
        """List business expenses with optional filters."""
        from app.models import PurchaseExpense
        from datetime import date as date_type

        query = self.db.query(PurchaseExpense).filter(
            PurchaseExpense.tenant_id == self.tenant_id
        )

        # Date filters
        start = args.get("start_date")
        end = args.get("end_date")
        if start:
            try:
                query = query.filter(PurchaseExpense.expense_date >= date_type.fromisoformat(start))
            except ValueError:
                pass
        if end:
            try:
                query = query.filter(PurchaseExpense.expense_date <= date_type.fromisoformat(end))
            except ValueError:
                pass

        # Category filter
        category = args.get("category")
        if category:
            query = query.filter(PurchaseExpense.category == category)

        # Capital only filter
        if args.get("capital_only"):
            query = query.filter(PurchaseExpense.is_capital == "true")

        expenses = query.order_by(PurchaseExpense.expense_date.desc()).all()
        if not expenses:
            return "No expenses recorded yet."

        total = sum(e.amount for e in expenses)

        # Group by category for summary
        by_cat = {}
        for e in expenses:
            cat = e.category or "other"
            by_cat[cat] = by_cat.get(cat, Decimal("0")) + e.amount

        lines = [f"*Expenses* — Total: ₹{total:.2f}\n"]

        # Category breakdown
        if len(by_cat) > 1:
            lines.append("*By category:*")
            for cat, amt in sorted(by_cat.items(), key=lambda x: -x[1]):
                lines.append(f"  {cat}: ₹{amt:.2f}")
            lines.append("")

        # Individual entries
        for e in expenses:
            label = e.description or e.notes or ""
            line = f"• {e.expense_date} — ₹{e.amount:.2f} [{e.category}]"
            if label:
                line += f" — {label[:60]}"
            if e.vendor_name:
                line += f" ({e.vendor_name})"
            if e.is_capital == "true":
                line += " 📦"
            lines.append(line)
        return "\n".join(lines)

    async def _tool_list_product_categories(self, args):
        """Return distinct product categories — fast alternative to list_products for booth setup."""
        from app.services.product_service import ProductService
        svc = ProductService(self.db)
        products = svc.list_products(self.tenant_id)
        if not products:
            return "No products in catalog yet."
        categories = sorted({p.category for p in products if p.category})
        if not categories:
            return "No categories found."
        # Return as CHOOSE: so the agent can present clickable buttons
        # Include "All categories" and "Done / Create booth" options
        options = "\n".join(categories)
        return (
            f"CHOOSE:Which categories are you bringing to the event?\n"
            f"{options}\n"
            f"All categories\n"
            f"Done — create booth with selected"
        )

    async def _tool_create_booth_from_categories(self, args):
        """Create a booth session with all products from the specified categories.
        Pass categories=["all"] to include everything."""
        from app.booth.booth_service import BoothService
        from app.services.product_service import ProductService
        from app.config import settings
        from decimal import Decimal

        session_name = args["name"]
        chosen_categories = [c.strip() for c in args.get("categories", [])]

        prod_svc = ProductService(self.db)
        all_products = prod_svc.list_products(self.tenant_id)

        # "all" or "all categories" → include everything
        include_all = any(c.lower() in ("all", "all categories") for c in chosen_categories)
        if include_all:
            selected = all_products
        else:
            chosen_lower = {c.lower() for c in chosen_categories}
            selected = [p for p in all_products if p.category and p.category.lower() in chosen_lower]

        booth_svc = BoothService(self.db, self.tenant_id)

        # End any existing active session first
        existing = booth_svc.get_active_session()
        if existing:
            booth_svc.end_session(existing.session_id)

        session = booth_svc.start_session(session_name)

        added = 0
        errors = []
        for product in selected:
            for variant in product.variants:
                try:
                    booth_svc.add_item(
                        session_id=session.session_id,
                        variant_id=variant.variant_id,
                        booth_price=Decimal(str(variant.price)),
                        stock_qty=None,
                    )
                    added += 1
                except Exception as e:
                    errors.append(f"{product.name}: {e}")

        booth_url = (
            f"{settings.WEBHOOK_URL}/booth/{self.tenant_id}"
            if settings.WEBHOOK_URL
            else f"http://localhost:8000/booth/{self.tenant_id}"
        )

        lines = [
            f"✅ Booth *{session_name}* is ready with {added} product variant(s).",
            f"\n🏪 Open on your phone to select products and start selling:\n{booth_url}",
        ]
        if errors:
            lines.append(f"\n⚠️ {len(errors)} items skipped.")
        return "\n".join(lines)

    async def _tool_create_booth_session(self, args):
        from app.booth.booth_service import BoothService
        from app.config import settings
        from decimal import Decimal
        from uuid import UUID

        svc = BoothService(self.db, self.tenant_id)
        session = svc.start_session(args["name"])

        # Add items
        added = []
        errors = []
        for item in args.get("items", []):
            try:
                stock = item.get("stock_qty")
                svc.add_item(
                    session_id=session.session_id,
                    variant_id=UUID(item["variant_id"]),
                    booth_price=Decimal(str(item["booth_price"])),
                    stock_qty=int(stock) if stock is not None else None,
                )
                added.append(item["variant_id"])
            except Exception as e:
                errors.append(str(e))

        booth_url = f"{settings.WEBHOOK_URL}/booth/{self.tenant_id}" if settings.WEBHOOK_URL else \
                    f"http://localhost:8000/booth/{self.tenant_id}"

        lines = [
            f"✅ Booth session '{session.name}' created with {len(added)} product(s).",
            f"\n🏪 Open your booth on any device:\n{booth_url}",
            "\nBookmark it — the link never changes.",
        ]
        if errors:
            lines.append(f"\n⚠️ Some items could not be added: {'; '.join(errors)}")
        return "\n".join(lines)

    async def _tool_add_booth_item(self, args):
        from app.booth.booth_service import BoothService
        from decimal import Decimal
        from uuid import UUID

        svc = BoothService(self.db, self.tenant_id)
        session = svc.get_active_session()
        if not session:
            return "No active booth session. Start one first."

        stock = args.get("stock_qty")
        item = svc.add_item(
            session_id=session.session_id,
            variant_id=UUID(args["variant_id"]),
            booth_price=Decimal(str(args["booth_price"])),
            stock_qty=int(stock) if stock is not None else None,
        )
        stock_str = f"{stock} units" if stock is not None else "unlimited"
        return (
            f"✅ Added {item.variant_id} to booth at ₹{item.booth_price} ({stock_str}).\n"
            "It's live on the sell screen now."
        )

    async def _tool_remove_booth_item(self, args):
        from app.booth.booth_service import BoothService
        from uuid import UUID

        svc = BoothService(self.db, self.tenant_id)
        session = svc.get_active_session()
        if not session:
            return "No active booth session."

        svc.remove_item(session.session_id, UUID(args["variant_id"]))
        return "✅ Item removed from booth."

    async def _tool_end_booth_session(self, args):
        from app.booth.booth_service import BoothService

        svc = BoothService(self.db, self.tenant_id)
        session = svc.get_active_session()
        if not session:
            return "No active booth session to end."

        closed = svc.end_session(session.session_id)
        summary = svc.get_session_summary(closed.session_id)

        duration = f"{summary.duration_minutes} min" if summary.duration_minutes else ""
        lines = [
            f"✅ '{summary.name}' session closed. {duration}",
            f"Sales: {summary.total_orders} · Revenue: ₹{summary.total_revenue:.0f} · "
            f"Items sold: {summary.items_sold}",
        ]
        if summary.top_products:
            lines.append("\nTop sellers:")
            for p in summary.top_products[:3]:
                lines.append(f"  • {p.product_name} — {p.units_sold} units · ₹{p.revenue:.0f}")
        return "\n".join(lines)

    async def _tool_get_booth_url(self, args):
        from app.config import settings
        if settings.WEBHOOK_URL:
            url = f"{settings.WEBHOOK_URL}/booth/{self.tenant_id}"
        else:
            url = f"http://localhost:8000/booth/{self.tenant_id}"

        from app.booth.booth_service import BoothService
        svc = BoothService(self.db, self.tenant_id)
        session = svc.get_active_session()
        if session:
            return f"🏪 Your booth is live ({session.name}):\n{url}"
        return f"🏪 Your booth URL:\n{url}\n\nNo active session — say 'set up booth' to create one."

    async def _tool_list_booth_sessions(self, args):
        from app.booth.booth_service import BoothService

        svc = BoothService(self.db, self.tenant_id)
        sessions = svc.list_sessions()
        if not sessions:
            return "No booth sessions yet."

        lines = []
        for s in sessions:
            status = "🟢 Active" if s.ended_at is None else "✅ Closed"
            summary = svc.get_session_summary(s.session_id)
            lines.append(
                f"{status} *{s.name}* — {s.started_at.strftime('%d %b %Y')} · "
                f"₹{summary.total_revenue:.0f} · {summary.total_orders} sales"
            )
        return "\n".join(lines)

    async def _tool_get_booth_session_summary(self, args):
        from app.booth.booth_service import BoothService

        svc = BoothService(self.db, self.tenant_id)
        search = args.get("session_name", "").lower().strip()
        sessions = svc.list_sessions()

        # Find best match by name
        match = next(
            (s for s in sessions if search in s.name.lower()),
            None
        )
        if not match:
            names = ", ".join(f"'{s.name}'" for s in sessions[:5])
            return f"No session found matching '{search}'. Available: {names}"

        summary = svc.get_session_summary(match.session_id)
        duration = f"{summary.duration_minutes} min" if summary.duration_minutes else "ongoing"
        lines = [
            f"*{summary.name}*",
            f"Date: {summary.started_at.strftime('%d %b %Y')} · Duration: {duration}",
            f"Sales: {summary.total_orders} · Revenue: ₹{summary.total_revenue:.0f} · "
            f"Items sold: {summary.items_sold}",
        ]
        if summary.top_products:
            lines.append("\nTop sellers:")
            for p in summary.top_products:
                lines.append(f"  • {p.product_name} — {p.units_sold} units · ₹{p.revenue:.0f}")
        return "\n".join(lines)

    # ── Products ───────────────────────────────────────────────────────────

    async def _tool_add_product(self, args):
        """
        Add a product to the catalog with one or more size/price variants.
        After creation, check for matching recipes and suggest linking.
        """
        from app.services.product_service import ProductService, VariantInput
        svc = ProductService(self.db)

        variants = [
            VariantInput(size_label=v["size_label"], price=Decimal(str(v["price"])))
            for v in args.get("variants", [])
        ]

        product = svc.create_product(
            tenant_id=self.tenant_id,
            name=args["name"],
            variants=variants,
            description=args.get("description"),
            category=args.get("category"),
        )

        lines = [f"✅ Added *{product.name}*"]
        if product.category:
            lines[0] += f" ({product.category})"
        for v in product.variants:
            lines.append(f"  {v.size_label}: ₹{v.price:.0f}")

        # Suggest recipe link if matching recipes exist
        matches = svc.find_matching_recipes_for_product(self.tenant_id, product.name)
        if matches:
            if len(matches) == 1:
                lines.append(
                    f"\nCHOOSE:💡 Found a matching recipe — link it to *{product.name}*?\n"
                    f"Yes, link {matches[0].name}\n"
                    f"No, skip"
                )
            else:
                options = "\n".join(r.name for r in matches[:5])
                lines.append(f"\nCHOOSE:💡 Which recipe belongs to *{product.name}*?\n{options}\nNone of these")

        return "\n".join(lines)

    async def _tool_list_products(self, args):
        from app.services.product_service import ProductService
        svc = ProductService(self.db)
        category = args.get("category")
        products = svc.list_products(self.tenant_id)
        if category:
            products = [p for p in products if p.category and category.lower() in p.category.lower()]
        if not products:
            return "No products in catalog yet."
        return svc.format_catalog(self.tenant_id) if not category else "\n\n".join(
            svc.format_product(p) for p in products
        )

    async def _tool_get_product(self, args):
        from app.services.product_service import ProductService
        svc = ProductService(self.db)
        product = svc.get_product(self.tenant_id, args["name"])
        if not product:
            return f"Product '{args['name']}' not found"
        return svc.format_product(product)

    async def _tool_update_product_price(self, args):
        from app.services.product_service import ProductService
        svc = ProductService(self.db)
        variant = svc.update_variant_price(
            self.tenant_id,
            args["name"],
            args["size_label"],
            Decimal(str(args["price"])),
        )
        return f"Updated {args['name']} — {variant.size_label}: ₹{variant.price:.0f}"

    async def _tool_add_product_variant(self, args):
        from app.services.product_service import ProductService
        svc = ProductService(self.db)
        variant = svc.add_variant(
            self.tenant_id,
            args["name"],
            args["size_label"],
            Decimal(str(args["price"])),
        )
        return f"Added variant to {args['name']}: {variant.size_label} = ₹{variant.price:.0f}"

    async def _tool_link_product_recipe(self, args):
        """Link a product to a recipe for cost/margin calculation."""
        from app.services.product_service import ProductService
        from app.services.recipe_service import RecipeService
        svc = ProductService(self.db)
        recipe_svc = RecipeService(self.db)

        recipe = recipe_svc.get_recipe(self.tenant_id, args["recipe_name"])
        if not recipe:
            return f"Recipe '{args['recipe_name']}' not found"

        product = svc.link_recipe(self.tenant_id, args["product_name"], recipe.recipe_id)
        return (
            f"✅ Linked *{product.name}* → recipe *{recipe.name}*\n"
            f"Cost per unit from recipe will now be used for margin calculation."
        )

    async def _tool_delete_product(self, args):
        from app.services.product_service import ProductService
        svc = ProductService(self.db)
        svc.delete_product(self.tenant_id, args["name"])
        return f"Product '{args['name']}' deleted from catalog"

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

    # ── Invoice ────────────────────────────────────────────────────────────

    async def _tool_generate_invoice(self, args):
        """
        Generate a PDF invoice for an order.

        Returns a special INVOICE: marker so RequestHandler knows to send
        a file rather than a text message.
        """
        from app.services.invoice_service import InvoiceService
        from app.services.order_finder import OrderFinder
        from app.database import get_registry_db
        from app.models import Tenant

        customer_identifier = args["customer_identifier"]
        delivery_date_str = args.get("delivery_date")

        # Resolve customer
        cust_svc = CustomerService(self.db)
        customers = cust_svc.get_customer(self.tenant_id, customer_identifier)
        if not customers:
            return f"No customer found matching '{customer_identifier}'"
        if len(customers) > 1:
            names = ", ".join(f"{c.name} ({c.phone})" for c in customers)
            return f"Multiple customers match: {names}. Please be more specific."

        customer = customers[0]

        # Resolve order — default to most recent if no delivery_date given
        finder = OrderFinder(self.db)
        if delivery_date_str:
            order = finder.find_by_customer_and_date(
                self.tenant_id, customer.customer_id, date.fromisoformat(delivery_date_str)
            )
            if not order:
                return f"No order found for {customer.name} on {delivery_date_str}"
        else:
            orders = finder.find_by_customer(self.tenant_id, customer.customer_id)
            if not orders:
                return f"No orders found for {customer.name}"
            if len(orders) > 1:
                # Use most recent order automatically
                order = sorted(orders, key=lambda o: o.delivery_date, reverse=True)[0]
            else:
                order = orders[0]

        # Get business name and currency from registry
        reg_db = next(get_registry_db())
        try:
            tenant = reg_db.query(Tenant).filter(Tenant.tenant_id == self.tenant_id).first()
            business_name = tenant.business_name if tenant else "My Business"
            from app.services.tenant_service import TenantService
            currency = TenantService.currency_for_country(tenant.country or "india") if tenant else "Rs."
        finally:
            reg_db.close()

        # Generate PDF
        svc = InvoiceService()
        invoice_data = svc.build_invoice_data(self.db, self.tenant_id, order.order_id, business_name, currency)
        pdf_bytes = svc.generate(invoice_data)

        filename = f"invoice_{invoice_data.invoice_number}_{customer.name.replace(' ', '_')}.pdf"

        # Store PDF bytes for the handler to retrieve and send as a file
        # Use a special return format the handler recognises
        import base64
        encoded = base64.b64encode(pdf_bytes).decode()
        return f"INVOICE_PDF:{filename}:{encoded}"
