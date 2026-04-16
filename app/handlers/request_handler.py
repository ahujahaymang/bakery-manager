"""
Platform-agnostic request handler for all business operations.

This handler contains all the business logic for processing user requests.
It can be used with Telegram, WhatsApp, or any other messaging platform.
"""

import logging
from typing import Optional
from datetime import date
from decimal import Decimal
from uuid import UUID
from dataclasses import dataclass

from app.services import (
    CustomerService,
    InventoryService,
    RecipeService,
    OrderService,
    PaymentService,
    ReportingService,
    ConversationService,
    ConversationState
)
from app.helpers import DisambiguationHelper, OrderFinder, TelegramFormatter

logger = logging.getLogger(__name__)


# Define dataclasses for service inputs
@dataclass
class InventoryItemCreate:
    name: str
    category: str
    quantity: Decimal
    unit: str
    cost_per_unit: Decimal


@dataclass
class RecipeComponentCreate:
    item_name: str
    quantity: Decimal
    component_type: str


@dataclass
class OrderItemCreate:
    recipe_name: str
    quantity: int
    selling_price: Decimal


@dataclass
class OrderCreate:
    customer_identifier: str
    delivery_date: date
    items: list


@dataclass
class PaymentCreate:
    order_identifier: str
    amount: Decimal
    method: str


class RequestHandler:
    """
    Platform-agnostic handler for all business operations.
    
    Contains all the business logic for processing user requests including:
    - Customer operations
    - Inventory operations
    - Recipe operations
    - Order operations
    - Payment operations
    - Reporting operations
    
    This is completely independent of the messaging platform.
    """
    
    def __init__(self, conversation_service: ConversationService):
        """
        Initialize request handler.
        
        Args:
            conversation_service: Service for managing conversation state
        """
        self.conversation_service = conversation_service
    
    # Customer handlers
    async def handle_create_customer(self, db, tenant_id: UUID, entities: dict) -> str:
        service = CustomerService(db)
        name = entities.get("name", "")
        phone = entities.get("phone", "")
        
        customer = service.create_customer(tenant_id, name, phone)
        return f"✅ Customer created successfully!\n\n*Name:* {customer.name}\n*Phone:* {customer.phone}"
    
    async def handle_get_customer(self, db, tenant_id: UUID, entities: dict) -> str:
        service = CustomerService(db)
        search = entities.get("name") or entities.get("phone") or entities.get("customer_identifier", "")
        
        customers = service.get_customer(tenant_id, search)
        
        if len(customers) == 0:
            return f"❌ No customer found matching '{search}'"
        elif len(customers) == 1:
            c = customers[0]
            return f"📋 Customer found:\n\n*Name:* {c.name}\n*Phone:* {c.phone}"
        else:
            result = f"📋 Found {len(customers)} customers:\n\n"
            for c in customers:
                result += f"• {c.name} ({c.phone})\n"
            return result
    
    async def handle_list_customers(self, db, tenant_id: UUID) -> str:
        service = CustomerService(db)
        customers = service.list_customers(tenant_id)
        
        if not customers:
            return "📋 No customers found"
        
        result = f"📋 *All Customers* ({len(customers)}):\n\n"
        for c in customers:
            result += f"• {c.name} - {c.phone}\n"
        return result
    
    # Inventory handlers
    async def handle_add_inventory(self, db, tenant_id: UUID, entities: dict) -> str:
        service = InventoryService(db)
        
        created_item = service.create_item(
            tenant_id=tenant_id,
            name=entities.get("name", ""),
            category=entities.get("category", ""),
            quantity=Decimal(str(entities.get("quantity", 0))),
            unit=entities.get("unit", ""),
            cost_per_unit=Decimal(str(entities.get("cost_per_unit", 0)))
        )
        
        return f"✅ Inventory item added!\n\n*Name:* {created_item.name}\n*Category:* {created_item.category}\n*Quantity:* {created_item.quantity} {created_item.unit}\n*Cost:* ₹{created_item.cost_per_unit}/{created_item.unit}"
    
    async def handle_update_inventory(self, db, tenant_id: UUID, entities: dict) -> str:
        service = InventoryService(db)
        name = entities.get("name", "")
        updates = {}
        
        if "quantity" in entities:
            updates["quantity"] = entities["quantity"]
        if "cost_per_unit" in entities:
            updates["cost_per_unit"] = entities["cost_per_unit"]
        
        item = service.update_item(tenant_id, name, updates)
        return f"✅ Inventory updated!\n\n*Name:* {item.name}\n*Quantity:* {item.quantity} {item.unit}\n*Cost:* ₹{item.cost_per_unit}/{item.unit}"
    
    async def handle_check_stock(self, db, tenant_id: UUID, entities: dict) -> str:
        service = InventoryService(db)
        name = entities.get("name", "")
        
        item = service.get_item(tenant_id, name)
        return f"📦 *Stock Level:*\n\n*Item:* {item.name}\n*Category:* {item.category}\n*Quantity:* {item.quantity} {item.unit}\n*Cost:* ₹{item.cost_per_unit}/{item.unit}"
    
    async def handle_list_inventory(self, db, tenant_id: UUID, entities: dict = None) -> str:
        service = InventoryService(db)
        items_by_category = service.list_items(tenant_id)
        
        # Check if category filter is specified
        category_filter = entities.get("category") if entities else None
        logger.info(f"Inventory filter: {category_filter}, entities: {entities}")
        logger.info(f"Items by category: {items_by_category}")
        
        # Check if there are any items (using correct keys: "ingredient" and "packaging")
        if not items_by_category.get("ingredient") and not items_by_category.get("packaging"):
            return "📦 No inventory items found"
        
        result = "📦 *Inventory:*\n\n"
        
        # Show only requested category or all
        if not category_filter or category_filter == "ingredient":
            if items_by_category.get("ingredient"):
                result += "*Ingredients:*\n"
                for item in items_by_category["ingredient"]:
                    result += f"• {item.name}: {item.quantity} {item.unit} @ ₹{item.cost_per_unit}/{item.unit}\n"
                result += "\n"
        
        if not category_filter or category_filter == "packaging":
            if items_by_category.get("packaging"):
                result += "*Packaging:*\n"
                for item in items_by_category["packaging"]:
                    result += f"• {item.name}: {item.quantity} {item.unit} @ ₹{item.cost_per_unit}/{item.unit}\n"
        
        return result
    
    # Recipe handlers
    async def handle_create_recipe(self, db, tenant_id: UUID, entities: dict) -> str:
        service = RecipeService(db)
        name = entities.get("name", "")
        yield_per_batch = int(entities.get("yield_per_batch", 1))
        
        recipe = service.create_recipe(tenant_id, name, yield_per_batch)
        return f"✅ Recipe created!\n\n*Name:* {recipe.name}\n*Yield:* {recipe.yield_per_batch} units per batch"
    
    async def handle_add_recipe_component(self, db, tenant_id: UUID, entities: dict) -> str:
        service = RecipeService(db)
        recipe_name = entities.get("recipe_name", "")
        component = RecipeComponentCreate(
            item_name=entities.get("item_name", ""),
            quantity=Decimal(str(entities.get("quantity", 0))),
            component_type=entities.get("component_type", "")
        )
        
        service.add_component(tenant_id, recipe_name, component)
        return f"✅ Component added to recipe '{recipe_name}'!\n\n*Item:* {component.item_name}\n*Quantity:* {component.quantity}\n*Type:* {component.component_type}"
    
    async def handle_calculate_recipe_cost(self, db, tenant_id: UUID, entities: dict) -> str:
        service = RecipeService(db)
        recipe_name = entities.get("recipe_name", "")
        
        cost = service.calculate_cost(tenant_id, recipe_name)
        return f"💰 *Recipe Cost for '{recipe_name}':*\n\n*Ingredient Cost:* ₹{cost.ingredient_cost:.2f}\n*Packaging Cost:* ₹{cost.packaging_cost:.2f}\n*Unit Cost:* ₹{cost.unit_cost:.2f}"

    
    # Order handlers
    async def handle_create_order(self, db, tenant_id: UUID, entities: dict, chat_id: str = None) -> str:
        from app.services import RecipeService, CustomerService
        
        service = OrderService(db)
        recipe_service = RecipeService(db)
        customer_service = CustomerService(db)
        
        # STEP 1: Check for customer disambiguation FIRST
        customer_identifier = entities.get("customer_identifier", "")
        customers = customer_service.get_customer(tenant_id, customer_identifier)
        
        if len(customers) > 1 and chat_id:
            # Multiple customers match - ask for disambiguation FIRST
            customer_options = DisambiguationHelper.create_customer_options(customers)
            
            # Store order data in conversation context
            items_data = []
            for item_data in entities.get("items", []):
                items_data.append({
                    'recipe_name': item_data.get("recipe_name", ""),
                    'quantity': int(item_data.get("quantity", 1)),
                    'selling_price': float(item_data.get("selling_price", 0))
                })
            
            order_data_dict = {
                'customer_identifier': customer_identifier,
                'delivery_date': entities.get("delivery_date", ""),  # May be empty
                'items': items_data
            }
            
            logger.info(f"Setting customer disambiguation state with order_data: {order_data_dict}")
            
            self.conversation_service.set_state(
                chat_id=chat_id,
                state=ConversationState.AWAITING_CUSTOMER_DISAMBIGUATION,
                pending_action="create_order",
                context_data={
                    'customer_options': customer_options,
                    'order_data': order_data_dict
                }
            )
            
            return DisambiguationHelper.create_customer_prompt(customer_identifier, customers)
        
        # STEP 2: Check if delivery date is provided
        delivery_date_str = entities.get("delivery_date", "")
        if not delivery_date_str and chat_id:
            # Store order data and set conversation state
            items_data = []
            for item_data in entities.get("items", []):
                items_data.append({
                    'recipe_name': item_data.get("recipe_name", ""),
                    'quantity': int(item_data.get("quantity", 1)),
                    'selling_price': float(item_data.get("selling_price", 0))
                })
            
            order_data_dict = {
                'customer_identifier': entities.get("customer_identifier", ""),
                'items': items_data
            }
            
            self.conversation_service.set_state(
                chat_id=chat_id,
                state=ConversationState.AWAITING_DELIVERY_DATE,
                pending_action="create_order",
                context_data={'order_data': order_data_dict}
            )
            
            customer_name = entities.get("customer_identifier", "")
            items_desc = ", ".join([f"{item.get('quantity', 1)} {item.get('recipe_name', '')}" 
                                   for item in entities.get("items", [])])
            return (
                f"📅 When should this order be delivered?\n\n"
                f"*Customer:* {customer_name}\n"
                f"*Items:* {items_desc}\n\n"
                f"Please provide the delivery date.\n"
                f"Example: \"tomorrow\", \"April 20\", or \"2026-04-20\""
            )
        
        # Parse delivery date
        delivery_date = date.fromisoformat(delivery_date_str)
        
        # Parse order items
        items = []
        for item_data in entities.get("items", []):
            items.append(OrderItemCreate(
                recipe_name=item_data.get("recipe_name", ""),
                quantity=int(item_data.get("quantity", 1)),
                selling_price=Decimal(str(item_data.get("selling_price", 0)))
            ))
        
        # Check for ambiguous recipes before creating order
        for i, item in enumerate(items):
            matching_recipes = recipe_service.search_recipes(tenant_id, item.recipe_name)
            
            # If multiple recipes match, ask for disambiguation
            if len(matching_recipes) > 1 and chat_id:
                recipe_options = DisambiguationHelper.create_recipe_options(matching_recipes)
                
                # Store order data in conversation context
                order_data_dict = {
                    'customer_identifier': entities.get("customer_identifier", ""),
                    'delivery_date': delivery_date.isoformat(),
                    'items': [
                        {
                            'recipe_name': it.recipe_name,
                            'quantity': it.quantity,
                            'selling_price': float(it.selling_price)
                        }
                        for it in items
                    ]
                }
                
                self.conversation_service.set_state(
                    chat_id=chat_id,
                    state=ConversationState.AWAITING_RECIPE_DISAMBIGUATION,
                    pending_action="create_order",
                    context_data={
                        'recipe_options': recipe_options,
                        'order_data': order_data_dict,
                        'item_index': i
                    }
                )
                
                return DisambiguationHelper.create_recipe_prompt(item.recipe_name, matching_recipes)
        
        order_data = OrderCreate(
            customer_identifier=entities.get("customer_identifier", ""),
            delivery_date=delivery_date,
            items=items
        )
        
        try:
            order = service.create_order(tenant_id, order_data)
            
            result = f"✅ Order created!\n\n*Delivery Date:* {order.delivery_date}\n*Status:* {order.status}\n\n*Items:*\n"
            for item in items:
                result += f"• {item.recipe_name} x{item.quantity} @ ₹{item.selling_price}\n"
            
            # Check if there are missing recipes
            if hasattr(order, '_missing_recipes') and order._missing_recipes:
                result += f"\n⚠️ *Warning:* The following recipes don't exist:\n"
                for recipe_name in order._missing_recipes:
                    result += f"• {recipe_name}\n"
                result += f"\nWithout recipes, you won't be able to:\n"
                result += f"• Track ingredient costs\n"
                result += f"• Calculate profit accurately\n"
                result += f"• Manage inventory usage\n\n"
                result += f"💡 Add the recipe for better tracking!"
            
            return result
        except ValueError as e:
            error_msg = str(e)
            # Check if it's a customer not found error
            if "No customer found" in error_msg and chat_id:
                customer_name = entities.get("customer_identifier", "")
                
                # Store order data in conversation context
                order_data_dict = {
                    'customer_identifier': customer_name,
                    'delivery_date': delivery_date.isoformat(),
                    'items': [
                        {
                            'recipe_name': it.recipe_name,
                            'quantity': it.quantity,
                            'selling_price': float(it.selling_price)
                        }
                        for it in items
                    ]
                }
                
                self.conversation_service.set_state(
                    chat_id=chat_id,
                    state=ConversationState.AWAITING_CUSTOMER_PHONE,
                    pending_action="create_order",
                    context_data={
                        'customer_name': customer_name,
                        'order_data': order_data_dict
                    }
                )
                
                return (
                    f"❌ Customer '*{customer_name}*' not found.\n\n"
                    f"Please provide {customer_name}'s phone number to add them as a customer.\n\n"
                    f"Example: 9876543210"
                )
            else:
                # Other error - just return the message
                return f"❌ Error: {error_msg}"
    
    async def handle_mark_delivered(self, db, tenant_id: UUID, entities: dict) -> str:
        service = OrderService(db)
        order_id = UUID(entities.get("order_id", ""))
        
        order = service.mark_delivered(tenant_id, order_id)
        return f"✅ Order marked as delivered!\n\n*Order ID:* `{order.order_id}`\n*Status:* {order.status}"
    
    async def handle_cancel_order(self, db, tenant_id: UUID, entities: dict, chat_id: str = None) -> str:
        """
        Cancel an order by marking it as 'cancelled'.
        Keeps the order in database for analysis and reporting.
        Used when customer cancels their order.
        """
        from app.services import CustomerService
        from app.models import Order, Payment, Customer
        
        order_finder = OrderFinder(db)
        order_id_str = entities.get("order_id")
        customer_identifier = entities.get("customer_identifier")
        delivery_date_str = entities.get("delivery_date")
        
        order = None
        
        # Try to find order by ID first
        if order_id_str:
            try:
                order_id = UUID(order_id_str)
                order = order_finder.find_by_id(tenant_id, order_id)
                if not order:
                    return f"❌ Order not found"
            except ValueError:
                return f"❌ Invalid order ID format: {order_id_str}"
        
        # If not found by ID, try customer + delivery_date
        elif customer_identifier:
            customer_service = CustomerService(db)
            customers = customer_service.get_customer(tenant_id, customer_identifier)
            
            if len(customers) == 0:
                return f"❌ No customer found matching '{customer_identifier}'"
            elif len(customers) > 1 and chat_id:
                # Multiple customers - use disambiguation
                customer_options = DisambiguationHelper.create_customer_options(customers)
                
                self.conversation_service.set_state(
                    chat_id=chat_id,
                    state=ConversationState.AWAITING_CUSTOMER_DISAMBIGUATION,
                    pending_action="cancel_order",
                    context_data={
                        'customer_options': customer_options,
                        'delivery_date': delivery_date_str
                    }
                )
                
                return DisambiguationHelper.create_customer_prompt(customer_identifier, customers)
            
            customer = customers[0]
            
            if delivery_date_str:
                # Find order for this customer on specific date
                delivery_date = date.fromisoformat(delivery_date_str)
                order = order_finder.find_by_customer_and_date(tenant_id, customer.customer_id, delivery_date)
            else:
                # Find all pending orders for this customer
                orders = order_finder.find_by_customer(tenant_id, customer.customer_id, status_filter='pending')
                
                if len(orders) == 0:
                    return f"❌ No pending orders found for {customer.name}"
                elif len(orders) == 1:
                    order = orders[0]
                else:
                    # Multiple pending orders - show options
                    result = order_finder.format_order_list_for_selection(orders, customer.name)
                    result += f"\nWhich order should I cancel? Please specify the delivery date.\nExample: 'cancel order for {customer.name} on {orders[0].delivery_date}'"
                    return result
        else:
            return "❌ Please provide either an order ID or customer name"
        
        if not order:
            return "❌ Order not found"
        
        # Check if order is already cancelled or delivered
        if order.status == 'cancelled':
            return f"❌ This order is already cancelled"
        if order.status == 'delivered':
            return f"❌ Cannot cancel a delivered order"
        
        # Check if order has payments
        payments = db.query(Payment).filter(Payment.order_id == order.order_id).all()
        payment_warning = None
        if payments:
            total_paid = sum(p.amount for p in payments)
            payment_warning = TelegramFormatter.format_payment_warning(total_paid)
        
        # Mark order as cancelled
        order.status = 'cancelled'
        db.commit()
        
        customer_name, _ = order_finder.get_order_details_for_display(order)
        
        return TelegramFormatter.format_order_cancelled(customer_name, order.delivery_date, payment_warning)
    
    async def handle_delete_order(self, db, tenant_id: UUID, entities: dict, chat_id: str = None) -> str:
        """
        Permanently delete an order from database.
        Used only for data entry mistakes.
        Cannot delete orders with payments.
        """
        from app.services import CustomerService
        from app.models import Order, OrderItem, Payment
        
        order_finder = OrderFinder(db)
        order_id_str = entities.get("order_id")
        customer_identifier = entities.get("customer_identifier")
        delivery_date_str = entities.get("delivery_date")
        
        order = None
        
        # Try to find order by ID first
        if order_id_str:
            try:
                order_id = UUID(order_id_str)
                order = order_finder.find_by_id(tenant_id, order_id)
                if not order:
                    return f"❌ Order not found"
            except ValueError:
                return f"❌ Invalid order ID format: {order_id_str}"
        
        # If not found by ID, try customer + delivery_date
        elif customer_identifier:
            customer_service = CustomerService(db)
            customers = customer_service.get_customer(tenant_id, customer_identifier)
            
            if len(customers) == 0:
                return f"❌ No customer found matching '{customer_identifier}'"
            elif len(customers) > 1:
                customer_list = [f"{c.name} ({c.phone})" for c in customers]
                return f"❌ Multiple customers match '{customer_identifier}': {', '.join(customer_list)}. Please be more specific."
            
            customer = customers[0]
            
            if delivery_date_str:
                # Find order for this customer on specific date
                delivery_date = date.fromisoformat(delivery_date_str)
                order = order_finder.find_by_customer_and_date(tenant_id, customer.customer_id, delivery_date)
            else:
                # Find all orders for this customer (any status)
                orders = order_finder.find_by_customer(tenant_id, customer.customer_id)
                
                if len(orders) == 0:
                    return f"❌ No orders found for {customer.name}"
                elif len(orders) == 1:
                    order = orders[0]
                else:
                    # Multiple orders - show options
                    result = order_finder.format_order_list_for_selection(orders, customer.name)
                    result += f"\nWhich order should I delete? Please specify the delivery date.\nExample: 'delete order for {customer.name} on {orders[0].delivery_date}'"
                    return result
        else:
            return "❌ Please provide either an order ID or customer name"
        
        if not order:
            return "❌ Order not found"
        
        # Check if order has payments - cannot delete if it has payments
        payments = db.query(Payment).filter(Payment.order_id == order.order_id).all()
        if payments:
            total_paid = sum(p.amount for p in payments)
            return TelegramFormatter.format_error_cannot_delete_order_with_payments(total_paid)
        
        # Get customer name and order details before deleting
        customer_name, items_desc = order_finder.get_order_details_for_display(order)
        delivery_date = order.delivery_date
        
        # Delete order items first (foreign key constraint)
        db.query(OrderItem).filter(OrderItem.order_id == order.order_id).delete()
        
        # Delete the order
        db.delete(order)
        db.commit()
        
        return TelegramFormatter.format_order_deleted(customer_name, delivery_date, items_desc)
    
    async def handle_upcoming_orders(self, db, tenant_id: UUID, entities: dict = None) -> str:
        service = OrderService(db)
        orders = service.get_upcoming_orders(tenant_id)
        
        # Check if filter is specified
        filter_type = entities.get("filter") if entities else None
        
        # Filter orders based on payment status if requested
        if filter_type:
            filtered_orders = []
            for order in orders:
                # Calculate payment status
                from app.services import PaymentService
                payment_service = PaymentService(db)
                
                # Get payments for this order - order_id is already a UUID
                from app.models import Payment
                payments = db.query(Payment).filter(
                    Payment.order_id == order['order_id']
                ).all()
                
                amount_paid = sum(p.amount for p in payments)
                total_amount = Decimal(str(order['total_price']))
                
                is_paid = amount_paid >= total_amount
                is_delivered = order.get('status') == 'delivered'
                
                # Apply filter
                if filter_type == "paid" and is_paid:
                    filtered_orders.append(order)
                elif filter_type == "unpaid" and not is_paid:
                    filtered_orders.append(order)
                elif filter_type == "delivered" and is_delivered:
                    filtered_orders.append(order)
                elif filter_type == "pending" and not is_delivered:
                    filtered_orders.append(order)
            
            orders = filtered_orders
        
        if not orders:
            filter_msg = f" {filter_type}" if filter_type else ""
            return f"📅 No{filter_msg} orders found"
        
        filter_label = f" {filter_type.title()}" if filter_type else ""
        result = f"📅 *{filter_label} Orders* ({len(orders)}):\n\n"
        for order in orders:
            result += f"*{order['customer_name']}* ({order['customer_phone']})\n"
            result += f"📆 Delivery: {order['delivery_date']}\n"
            result += f"\n*Items:*\n"
            for item in order['items']:
                result += f"  • {item['recipe_name']} x{item['quantity']} @ ₹{item['selling_price']} = ₹{item['item_total']}\n"
            result += f"\n💰 *Total: ₹{order['total_price']}*\n"
            result += f"━━━━━━━━━━━━━━━━\n\n"
        
        return result
    
    async def handle_unpaid_orders(self, db, tenant_id: UUID) -> str:
        service = OrderService(db)
        orders = service.get_unpaid_orders(tenant_id)
        
        if not orders:
            return "💰 All orders are paid!"
        
        result = f"💰 *Unpaid Orders* ({len(orders)}):\n\n"
        for order in orders:
            result += f"*{order['customer_name']}*\n"
            result += f"  Total: ₹{order['total_amount']}\n"
            result += f"  Paid: ₹{order['amount_paid']}\n"
            result += f"  *Due: ₹{order['amount_due']}*\n\n"
        
        return result
    
    # Payment handlers
    async def handle_record_payment(self, db, tenant_id: UUID, entities: dict) -> str:
        logger.info(f"Payment entities received: {entities}")
        service = PaymentService(db)
        
        # Handle both 'order_identifier' and 'customer_identifier' for backward compatibility
        order_identifier = entities.get("order_identifier") or entities.get("customer_identifier", "")
        
        payment_data = PaymentCreate(
            order_identifier=order_identifier,
            amount=Decimal(str(entities.get("amount", 0))),
            method=entities.get("method", "")
        )
        
        payment = service.record_payment(tenant_id, payment_data)
        return f"✅ Payment recorded!\n\n*Amount:* ₹{payment.amount}\n*Method:* {payment.method}"
    
    async def handle_payment_history(self, db, tenant_id: UUID, entities: dict) -> str:
        service = PaymentService(db)
        
        start_date = None
        end_date = None
        
        if "start_date" in entities:
            start_date = date.fromisoformat(entities["start_date"])
        if "end_date" in entities:
            end_date = date.fromisoformat(entities["end_date"])
        
        payments = service.get_payment_history(tenant_id, start_date, end_date)
        
        if not payments:
            return "💳 No payment history found"
        
        result = f"💳 *Payment History* ({len(payments)}):\n\n"
        for payment in payments:
            result += f"*{payment['customer_name']}*\n"
            result += f"  Amount: ₹{payment['amount']}\n"
            result += f"  Method: {payment['method']}\n"
            result += f"  Date: {payment['payment_date']}\n\n"
        
        return result
    
    # Reporting handlers
    async def handle_weekly_profit(self, db, tenant_id: UUID) -> str:
        service = ReportingService(db)
        report = service.calculate_weekly_profit(tenant_id)
        
        result = f"📊 *Weekly Profit Report*\n"
        result += f"Week: {report.week_start} to {report.week_end}\n\n"
        result += f"Revenue: ₹{report.total_revenue:.2f}\n"
        result += f"Ingredient Cost: ₹{report.total_ingredient_cost:.2f}\n"
        result += f"Packaging Cost: ₹{report.total_packaging_cost:.2f}\n"
        result += f"*Gross Profit: ₹{report.gross_profit:.2f}*"
        
        return result
