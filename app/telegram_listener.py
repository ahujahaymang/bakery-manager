"""
Telegram Bot Polling Listener.

This module implements a polling-based listener for Telegram messages,
allowing local testing without requiring a public webhook URL.
"""

import asyncio
import logging
from typing import Optional
from datetime import datetime, date
from decimal import Decimal
from uuid import UUID
from dataclasses import dataclass

from telegram import Update
from telegram.ext import Application, MessageHandler, filters, ContextTypes

from app.database import get_db
from app.services import (
    TenantService,
    CustomerService,
    InventoryService,
    RecipeService,
    OrderService,
    PaymentService,
    ReportingService,
    LLMService,
    Intent,
    IntentResult,
    ConversationService,
    ConversationState
)
from app.error_handler import ErrorHandler, format_error_for_telegram
from app.config import settings

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


class TelegramBotListener:
    """
    Telegram bot listener using polling mode.
    
    Polls Telegram for new messages and processes them through the
    service layer with LLM-based intent detection.
    """
    
    def __init__(self, bot_token: Optional[str] = None):
        """
        Initialize the Telegram bot listener.
        
        Args:
            bot_token: Telegram bot token (defaults to settings.TELEGRAM_BOT_TOKEN)
        """
        self.bot_token = bot_token or settings.TELEGRAM_BOT_TOKEN
        if not self.bot_token:
            raise ValueError("TELEGRAM_BOT_TOKEN must be configured")
        
        self.llm_service = LLMService()
        self.conversation_service = ConversationService()
        self.application = None
    
    async def handle_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """
        Handle incoming Telegram messages.
        
        Args:
            update: Telegram update object
            context: Telegram context object
        """
        try:
            # Extract message data
            if not update.message or not update.message.text:
                return
            
            chat_id = str(update.message.chat_id)
            text = update.message.text.strip()
            
            logger.info(f"Processing message from chat_id={chat_id}: {text}")
            
            # Get database session
            db = next(get_db())
            
            try:
                # Resolve or create tenant
                tenant_service = TenantService(db)
                tenant = tenant_service.get_or_create_tenant(chat_id)
                tenant_id = tenant.tenant_id
                
                logger.info(f"Tenant resolved: {tenant_id}")
                
                # Check if we're in the middle of a conversation
                conv_context = self.conversation_service.get_context(tenant_id, chat_id)
                
                if conv_context.state != ConversationState.IDLE:
                    # Handle conversation continuation
                    response_text = await self.handle_conversation_continuation(
                        db=db,
                        tenant_id=tenant_id,
                        chat_id=chat_id,
                        text=text,
                        conv_context=conv_context
                    )
                else:
                    # New conversation - detect intent using LLM
                    intent_result = await self.llm_service.detect_intent(text)
                    logger.info(f"Intent detected: {intent_result.intent} (confidence: {intent_result.confidence})")
                    
                    # Check confidence
                    if not self.llm_service.is_confident(intent_result):
                        clarification = self.llm_service.get_clarification_message(intent_result)
                        await update.message.reply_text(clarification)
                        return
                    
                    # Route to appropriate service based on intent
                    response_text = await self.route_intent(
                        db=db,
                        tenant_id=tenant_id,
                        chat_id=chat_id,
                        intent_result=intent_result
                    )
                
                # Send response to user - try with Markdown first, fallback to plain text
                try:
                    await update.message.reply_text(response_text, parse_mode='Markdown')
                except Exception as markdown_error:
                    # If Markdown parsing fails, send as plain text
                    logger.warning(f"Markdown parsing failed, sending as plain text: {markdown_error}")
                    await update.message.reply_text(response_text)
                
            finally:
                db.close()
        
        except Exception as e:
            logger.error(f"Error processing message: {e}", exc_info=True)
            
            # Try to send error message to user
            try:
                error_response = ErrorHandler.handle_exception(e)
                error_message = format_error_for_telegram(error_response)
                # Send error without markdown to avoid parsing issues
                await update.message.reply_text(error_message)
            except Exception as send_error:
                logger.error(f"Failed to send error message: {send_error}")
    
    async def handle_conversation_continuation(
        self,
        db,
        tenant_id: UUID,
        chat_id: str,
        text: str,
        conv_context
    ) -> str:
        """
        Handle continuation of an ongoing conversation.
        
        Args:
            db: Database session
            tenant_id: UUID of the tenant
            chat_id: Telegram chat ID
            text: User's message text
            conv_context: Current conversation context
        
        Returns:
            str: Response message for the user
        """
        logger.info(f"Handling conversation continuation: state={conv_context.state}")
        
        # Handle different conversation states
        if conv_context.state == ConversationState.AWAITING_CUSTOMER_PHONE:
            return await self.handle_awaiting_customer_phone(db, tenant_id, chat_id, text, conv_context)
        
        elif conv_context.state == ConversationState.AWAITING_CUSTOMER_DISAMBIGUATION:
            return await self.handle_awaiting_customer_disambiguation(db, tenant_id, chat_id, text, conv_context)
        
        elif conv_context.state == ConversationState.AWAITING_DELIVERY_DATE:
            return await self.handle_awaiting_delivery_date(db, tenant_id, chat_id, text, conv_context)
        
        elif conv_context.state == ConversationState.AWAITING_RECIPE_DISAMBIGUATION:
            return await self.handle_awaiting_recipe_disambiguation(db, tenant_id, chat_id, text, conv_context)
        
        else:
            # Unknown state - reset and process as new message
            self.conversation_service.reset_context(chat_id)
            intent_result = await self.llm_service.detect_intent(text)
            return await self.route_intent(db, tenant_id, chat_id, intent_result)
    
    async def route_intent(self, db, tenant_id: UUID, chat_id: str, intent_result: IntentResult) -> str:
        """
        Route intent to appropriate service and format response.
        
        Args:
            db: Database session
            tenant_id: UUID of the tenant
            intent_result: Detected intent and entities
        
        Returns:
            str: Formatted response message for Telegram
        """
        intent = intent_result.intent
        entities = intent_result.entities
        
        try:
            # Customer operations
            if intent == Intent.CREATE_CUSTOMER:
                return await self.handle_create_customer(db, tenant_id, entities)
            elif intent == Intent.GET_CUSTOMER:
                return await self.handle_get_customer(db, tenant_id, entities)
            elif intent == Intent.LIST_CUSTOMERS:
                return await self.handle_list_customers(db, tenant_id)
            
            # Inventory operations
            elif intent == Intent.ADD_INVENTORY:
                return await self.handle_add_inventory(db, tenant_id, entities)
            elif intent == Intent.UPDATE_INVENTORY:
                return await self.handle_update_inventory(db, tenant_id, entities)
            elif intent == Intent.CHECK_STOCK:
                return await self.handle_check_stock(db, tenant_id, entities)
            elif intent == Intent.LIST_INVENTORY:
                return await self.handle_list_inventory(db, tenant_id)
            
            # Recipe operations
            elif intent == Intent.CREATE_RECIPE:
                return await self.handle_create_recipe(db, tenant_id, entities)
            elif intent == Intent.ADD_RECIPE_COMPONENT:
                return await self.handle_add_recipe_component(db, tenant_id, entities)
            elif intent == Intent.CALCULATE_RECIPE_COST:
                return await self.handle_calculate_recipe_cost(db, tenant_id, entities)
            
            # Order operations
            elif intent == Intent.CREATE_ORDER:
                return await self.handle_create_order(db, tenant_id, entities, chat_id)
            elif intent == Intent.MARK_DELIVERED:
                return await self.handle_mark_delivered(db, tenant_id, entities)
            elif intent == Intent.UPCOMING_ORDERS:
                return await self.handle_upcoming_orders(db, tenant_id)
            elif intent == Intent.UNPAID_ORDERS:
                return await self.handle_unpaid_orders(db, tenant_id)
            
            # Payment operations
            elif intent == Intent.RECORD_PAYMENT:
                return await self.handle_record_payment(db, tenant_id, entities)
            elif intent == Intent.PAYMENT_HISTORY:
                return await self.handle_payment_history(db, tenant_id, entities)
            
            # Reporting operations
            elif intent == Intent.WEEKLY_PROFIT:
                return await self.handle_weekly_profit(db, tenant_id)
            
            else:
                return "I'm not sure how to help with that. Could you please rephrase your request?"
        
        except Exception as e:
            logger.error(f"Error handling intent {intent}: {e}", exc_info=True)
            error_response = ErrorHandler.handle_exception(e)
            # Return plain text for errors to avoid markdown parsing issues
            return format_error_for_telegram(error_response)
    
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
    
    async def handle_list_inventory(self, db, tenant_id: UUID) -> str:
        service = InventoryService(db)
        items_by_category = service.list_items(tenant_id)
        
        if not items_by_category.get("ingredients") and not items_by_category.get("packaging"):
            return "📦 No inventory items found"
        
        result = "📦 *Inventory:*\n\n"
        
        if items_by_category.get("ingredients"):
            result += "*Ingredients:*\n"
            for item in items_by_category["ingredients"]:
                result += f"• {item.name}: {item.quantity} {item.unit} @ ₹{item.cost_per_unit}/{item.unit}\n"
            result += "\n"
        
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
        
        # Check if delivery date is provided
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
        
        # Check for customer disambiguation BEFORE creating order
        customer_identifier = entities.get("customer_identifier", "")
        customers = customer_service.get_customer(tenant_id, customer_identifier)
        
        if len(customers) == 0:
            # Customer not found - will be handled by OrderService
            pass
        elif len(customers) > 1 and chat_id:
            # Multiple customers match - ask for disambiguation
            customer_options = [
                {'name': c.name, 'phone': c.phone, 'id': str(c.customer_id)}
                for c in customers
            ]
            
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
                'delivery_date': delivery_date.isoformat(),
                'items': items_data
            }
            
            self.conversation_service.set_state(
                chat_id=chat_id,
                state=ConversationState.AWAITING_CUSTOMER_DISAMBIGUATION,
                pending_action="create_order",
                context_data={
                    'customer_options': customer_options,
                    'order_data': order_data_dict
                }
            )
            
            result = f"🤔 I found multiple customers named '*{customer_identifier}*':\n\n"
            for idx, customer_dict in enumerate(customer_options, 1):
                result += f"{idx}. {customer_dict['name']} ({customer_dict['phone']})\n"
            result += f"\nWhich one? Reply with the number or phone number."
            
            return result
        
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
                recipe_options = [{'name': r.name, 'id': str(r.recipe_id)} for r in matching_recipes]
                
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
                
                result = f"🤔 I found multiple recipes matching '*{item.recipe_name}*':\n\n"
                for idx, recipe_dict in enumerate(recipe_options, 1):
                    result += f"{idx}. {recipe_dict['name']}\n"
                result += f"\nWhich one should I use? Reply with the number or full name."
                
                return result
        
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
    
    async def handle_upcoming_orders(self, db, tenant_id: UUID) -> str:
        service = OrderService(db)
        orders = service.get_upcoming_orders(tenant_id)
        
        if not orders:
            return "📅 No upcoming orders"
        
        result = f"📅 *Upcoming Orders* ({len(orders)}):\n\n"
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
    
    # Conversation state handlers
    async def handle_awaiting_customer_phone(
        self,
        db,
        tenant_id: UUID,
        chat_id: str,
        text: str,
        conv_context
    ) -> str:
        """
        Handle phone number input for customer creation.
        
        Creates customer with stored name and phone, then resumes order creation.
        """
        from app.services import CustomerService, RecipeService
        
        # Extract phone number from text (simple extraction)
        phone = ''.join(filter(str.isdigit, text))
        
        if not phone or len(phone) < 10:
            return "Please provide a valid phone number (at least 10 digits)."
        
        # Get stored customer name from context
        customer_name = conv_context.context_data.get('customer_name', '')
        
        if not customer_name:
            # Context lost, reset
            self.conversation_service.reset_context(chat_id)
            return "Sorry, I lost track of the conversation. Please start over."
        
        # Create customer
        customer_service = CustomerService(db)
        try:
            customer = customer_service.create_customer(tenant_id, customer_name, phone)
            
            # Get stored order data
            order_data_dict = conv_context.context_data.get('order_data', {})
            
            # Resume order creation
            order_service = OrderService(db)
            
            # Reconstruct order data
            items = []
            for item_dict in order_data_dict.get('items', []):
                items.append(OrderItemCreate(
                    recipe_name=item_dict['recipe_name'],
                    quantity=item_dict['quantity'],
                    selling_price=Decimal(str(item_dict['selling_price']))
                ))
            
            order_data = OrderCreate(
                customer_identifier=phone,  # Use phone now
                delivery_date=date.fromisoformat(order_data_dict['delivery_date']),
                items=items
            )
            
            order = order_service.create_order(tenant_id, order_data)
            
            # Reset conversation state
            self.conversation_service.reset_context(chat_id)
            
            # Format response
            result = f"✅ Customer added: {customer.name} ({customer.phone})\n\n"
            result += f"✅ Order created!\n\n*Delivery Date:* {order.delivery_date}\n*Status:* {order.status}\n\n*Items:*\n"
            for item in items:
                result += f"• {item.recipe_name} x{item.quantity} @ ₹{item.selling_price}\n"
            
            # Check for missing recipes
            if hasattr(order, '_missing_recipes') and order._missing_recipes:
                result += f"\n⚠️ *Warning:* The following recipes don't exist:\n"
                for recipe_name in order._missing_recipes:
                    result += f"• {recipe_name}\n"
                result += f"\n💡 Add the recipe for better tracking!"
            
            return result
            
        except ValueError as e:
            # Reset conversation on error
            self.conversation_service.reset_context(chat_id)
            return f"❌ Error: {str(e)}\n\nPlease start over."
    
    async def handle_awaiting_delivery_date(
        self,
        db,
        tenant_id: UUID,
        chat_id: str,
        text: str,
        conv_context
    ) -> str:
        """
        Handle delivery date input for order creation.
        
        Parses date from text and resumes order creation.
        """
        # Use LLM to parse the date
        try:
            # Simple date parsing - try to extract YYYY-MM-DD or use LLM
            import re
            date_match = re.search(r'(\d{4})-(\d{2})-(\d{2})', text)
            
            if date_match:
                delivery_date = date.fromisoformat(date_match.group(0))
            else:
                # Use LLM to parse natural language date
                intent_result = await self.llm_service.detect_intent(f"Order for delivery on {text}")
                delivery_date_str = intent_result.entities.get('delivery_date')
                
                if not delivery_date_str:
                    return f"I couldn't understand the date. Please provide it in a clear format like 'April 20' or '2026-04-20'."
                
                delivery_date = date.fromisoformat(delivery_date_str)
            
            # Validate date is not in the past
            if delivery_date < date.today():
                return f"❌ Delivery date cannot be in the past. Please provide a future date."
            
            # Get stored order data
            order_data_dict = conv_context.context_data.get('order_data', {})
            order_data_dict['delivery_date'] = delivery_date.isoformat()
            
            # Resume order creation
            order_service = OrderService(db)
            
            items = []
            for item_dict in order_data_dict.get('items', []):
                items.append(OrderItemCreate(
                    recipe_name=item_dict['recipe_name'],
                    quantity=item_dict['quantity'],
                    selling_price=Decimal(str(item_dict['selling_price']))
                ))
            
            order_data = OrderCreate(
                customer_identifier=order_data_dict['customer_identifier'],
                delivery_date=delivery_date,
                items=items
            )
            
            try:
                order = order_service.create_order(tenant_id, order_data)
                
                # Reset conversation state
                self.conversation_service.reset_context(chat_id)
                
                # Format response
                result = f"✅ Order created!\n\n*Delivery Date:* {order.delivery_date}\n*Status:* {order.status}\n\n*Items:*\n"
                for item in items:
                    result += f"• {item.recipe_name} x{item.quantity} @ ₹{item.selling_price}\n"
                
                # Check for missing recipes
                if hasattr(order, '_missing_recipes') and order._missing_recipes:
                    result += f"\n⚠️ *Warning:* The following recipes don't exist:\n"
                    for recipe_name in order._missing_recipes:
                        result += f"• {recipe_name}\n"
                    result += f"\n💡 Add the recipe for better tracking!"
                
                return result
                
            except ValueError as order_error:
                # Check if it's a customer not found error
                error_msg = str(order_error)
                if "No customer found" in error_msg:
                    customer_name = order_data_dict['customer_identifier']
                    
                    # Transition to AWAITING_CUSTOMER_PHONE state
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
                elif "Multiple customers match" in error_msg:
                    # Extract customer list from error message or query again
                    from app.services import CustomerService
                    customer_service = CustomerService(db)
                    customer_name = order_data_dict['customer_identifier']
                    customers = customer_service.get_customer(tenant_id, customer_name)
                    
                    customer_options = [
                        {'name': c.name, 'phone': c.phone, 'id': str(c.customer_id)}
                        for c in customers
                    ]
                    
                    # Transition to AWAITING_CUSTOMER_DISAMBIGUATION state
                    self.conversation_service.set_state(
                        chat_id=chat_id,
                        state=ConversationState.AWAITING_CUSTOMER_DISAMBIGUATION,
                        pending_action="create_order",
                        context_data={
                            'customer_options': customer_options,
                            'order_data': order_data_dict
                        }
                    )
                    
                    result = f"🤔 I found multiple customers named '*{customer_name}*':\n\n"
                    for idx, customer_dict in enumerate(customer_options, 1):
                        result += f"{idx}. {customer_dict['name']} ({customer_dict['phone']})\n"
                    result += f"\nWhich one? Reply with the number or phone number."
                    
                    return result
                else:
                    # Other error - reset and return message
                    self.conversation_service.reset_context(chat_id)
                    return f"❌ Error: {error_msg}\n\nPlease start over."
            
        except Exception as e:
            logger.error(f"Error parsing delivery date: {e}")
            return f"I couldn't understand the date. Please provide it in a clear format like 'April 20' or '2026-04-20'."
    
    async def handle_awaiting_customer_disambiguation(
        self,
        db,
        tenant_id: UUID,
        chat_id: str,
        text: str,
        conv_context
    ) -> str:
        """
        Handle customer selection from multiple matches.
        
        Parses user's choice and resumes order creation with selected customer.
        """
        from app.services import CustomerService
        
        # Get stored customer options
        customer_options = conv_context.context_data.get('customer_options', [])
        
        if not customer_options:
            # Context lost, reset
            self.conversation_service.reset_context(chat_id)
            return "Sorry, I lost track of the conversation. Please start over."
        
        # Parse user's choice (number or phone)
        text_lower = text.strip().lower()
        selected_customer = None
        
        # Try to parse as number
        try:
            choice_num = int(text_lower)
            if 1 <= choice_num <= len(customer_options):
                selected_customer = customer_options[choice_num - 1]
        except ValueError:
            # Not a number, try to match by phone
            for customer_dict in customer_options:
                if text_lower in customer_dict['phone'].lower():
                    selected_customer = customer_dict
                    break
        
        if not selected_customer:
            # Invalid choice
            result = "I didn't understand your choice. Please reply with:\n"
            for i, customer_dict in enumerate(customer_options, 1):
                result += f"{i}. {customer_dict['name']} ({customer_dict['phone']})\n"
            return result
        
        # Get stored order data
        order_data_dict = conv_context.context_data.get('order_data', {})
        
        # Update the customer identifier to use phone (unique)
        order_data_dict['customer_identifier'] = selected_customer['phone']
        
        # Resume order creation
        order_service = OrderService(db)
        
        items = []
        for item_dict in order_data_dict.get('items', []):
            items.append(OrderItemCreate(
                recipe_name=item_dict['recipe_name'],
                quantity=item_dict['quantity'],
                selling_price=Decimal(str(item_dict['selling_price']))
            ))
        
        order_data = OrderCreate(
            customer_identifier=selected_customer['phone'],
            delivery_date=date.fromisoformat(order_data_dict['delivery_date']),
            items=items
        )
        
        try:
            order = order_service.create_order(tenant_id, order_data)
            
            # Reset conversation state
            self.conversation_service.reset_context(chat_id)
            
            # Format response
            result = f"✅ Order created for *{selected_customer['name']}* ({selected_customer['phone']})!\n\n*Delivery Date:* {order.delivery_date}\n*Status:* {order.status}\n\n*Items:*\n"
            for item in items:
                result += f"• {item.recipe_name} x{item.quantity} @ ₹{item.selling_price}\n"
            
            # Check for missing recipes
            if hasattr(order, '_missing_recipes') and order._missing_recipes:
                result += f"\n⚠️ *Warning:* The following recipes don't exist:\n"
                for recipe_name in order._missing_recipes:
                    result += f"• {recipe_name}\n"
                result += f"\n💡 Add the recipe for better tracking!"
            
            return result
            
        except ValueError as e:
            # Reset conversation on error
            self.conversation_service.reset_context(chat_id)
            return f"❌ Error: {str(e)}\n\nPlease start over."
    
    async def handle_awaiting_recipe_disambiguation(
        self,
        db,
        tenant_id: UUID,
        chat_id: str,
        text: str,
        conv_context
    ) -> str:
        """
        Handle recipe selection from multiple matches.
        
        Parses user's choice and resumes order creation with selected recipe.
        """
        from app.services import RecipeService
        
        # Get stored recipe options
        recipe_options = conv_context.context_data.get('recipe_options', [])
        
        if not recipe_options:
            # Context lost, reset
            self.conversation_service.reset_context(chat_id)
            return "Sorry, I lost track of the conversation. Please start over."
        
        # Parse user's choice (number or name)
        text_lower = text.strip().lower()
        selected_recipe = None
        
        # Try to parse as number
        try:
            choice_num = int(text_lower)
            if 1 <= choice_num <= len(recipe_options):
                selected_recipe = recipe_options[choice_num - 1]
        except ValueError:
            # Not a number, try to match by name
            for recipe_dict in recipe_options:
                if text_lower in recipe_dict['name'].lower():
                    selected_recipe = recipe_dict
                    break
        
        if not selected_recipe:
            # Invalid choice
            result = "I didn't understand your choice. Please reply with:\n"
            for i, recipe_dict in enumerate(recipe_options, 1):
                result += f"{i}. {recipe_dict['name']}\n"
            return result
        
        # Get stored order data
        order_data_dict = conv_context.context_data.get('order_data', {})
        item_index = conv_context.context_data.get('item_index', 0)
        
        # Update the recipe name in the order data
        if 'items' in order_data_dict and item_index < len(order_data_dict['items']):
            order_data_dict['items'][item_index]['recipe_name'] = selected_recipe['name']
        
        # Resume order creation
        order_service = OrderService(db)
        
        items = []
        for item_dict in order_data_dict.get('items', []):
            items.append(OrderItemCreate(
                recipe_name=item_dict['recipe_name'],
                quantity=item_dict['quantity'],
                selling_price=Decimal(str(item_dict['selling_price']))
            ))
        
        order_data = OrderCreate(
            customer_identifier=order_data_dict['customer_identifier'],
            delivery_date=date.fromisoformat(order_data_dict['delivery_date']),
            items=items
        )
        
        try:
            order = order_service.create_order(tenant_id, order_data)
            
            # Reset conversation state
            self.conversation_service.reset_context(chat_id)
            
            # Format response
            result = f"✅ Order created with *{selected_recipe['name']}*!\n\n*Delivery Date:* {order.delivery_date}\n*Status:* {order.status}\n\n*Items:*\n"
            for item in items:
                result += f"• {item.recipe_name} x{item.quantity} @ ₹{item.selling_price}\n"
            
            return result
            
        except ValueError as e:
            # Reset conversation on error
            self.conversation_service.reset_context(chat_id)
            return f"❌ Error: {str(e)}\n\nPlease start over."
    
    async def start(self):
        """Start the bot in polling mode."""
        logger.info("Starting Telegram bot in polling mode...")
        
        # Create application
        self.application = Application.builder().token(self.bot_token).build()
        
        # Add message handler
        self.application.add_handler(
            MessageHandler(filters.TEXT & ~filters.COMMAND, self.handle_message)
        )
        
        # Initialize and start polling
        async with self.application:
            await self.application.initialize()
            await self.application.start()
            logger.info("Bot is now listening for messages...")
            await self.application.updater.start_polling()
            
            # Keep running until interrupted
            try:
                while True:
                    await asyncio.sleep(1)
            except (KeyboardInterrupt, asyncio.CancelledError):
                logger.info("Stopping bot...")
            finally:
                await self.application.updater.stop()
                await self.application.stop()
                await self.application.shutdown()
    
    async def stop(self):
        """Stop the bot."""
        await self.llm_service.close()


async def main():
    """Main entry point for the Telegram bot listener."""
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )
    
    listener = TelegramBotListener()
    
    try:
        await listener.start()
    except KeyboardInterrupt:
        logger.info("Shutting down bot...")
    except Exception as e:
        logger.error(f"Fatal error: {e}", exc_info=True)
    finally:
        await listener.stop()


if __name__ == "__main__":
    asyncio.run(main())
