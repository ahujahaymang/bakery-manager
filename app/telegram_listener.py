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
    IntentResult
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
                
                # Detect intent using LLM
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
    
    async def route_intent(self, db, tenant_id: UUID, intent_result: IntentResult) -> str:
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
                return await self.handle_create_order(db, tenant_id, entities)
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
    async def handle_create_order(self, db, tenant_id: UUID, entities: dict) -> str:
        service = OrderService(db)
        
        # Check if delivery date is provided
        delivery_date_str = entities.get("delivery_date", "")
        if not delivery_date_str:
            customer_name = entities.get("customer_identifier", "")
            items_desc = ", ".join([f"{item.get('quantity', 1)} {item.get('recipe_name', '')}" 
                                   for item in entities.get("items", [])])
            return (
                f"📅 When should this order be delivered?\n\n"
                f"*Customer:* {customer_name}\n"
                f"*Items:* {items_desc}\n\n"
                f"Please provide the delivery date.\n"
                f"Example: \"Deliver on 2026-04-20\" or \"Deliver tomorrow\""
            )
        
        # Parse order items
        items = []
        for item_data in entities.get("items", []):
            items.append(OrderItemCreate(
                recipe_name=item_data.get("recipe_name", ""),
                quantity=int(item_data.get("quantity", 1)),
                selling_price=Decimal(str(item_data.get("selling_price", 0)))
            ))
        
        # Parse delivery date
        delivery_date = date.fromisoformat(delivery_date_str)
        
        order_data = OrderCreate(
            customer_identifier=entities.get("customer_identifier", ""),
            delivery_date=delivery_date,
            items=items
        )
        
        try:
            order = service.create_order(tenant_id, order_data)
            
            result = f"✅ Order created!\n\n*Order ID:* `{order.order_id}`\n*Delivery Date:* {order.delivery_date}\n*Status:* {order.status}\n\n*Items:*\n"
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
            if "No customer found" in error_msg:
                customer_name = entities.get("customer_identifier", "")
                return (
                    f"❌ Customer '{customer_name}' not found.\n\n"
                    f"Please add the customer first:\n"
                    f"\"Add customer {customer_name} with phone [phone-number]\"\n\n"
                    f"Then try creating the order again."
                )
            else:
                raise
    
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
        service = PaymentService(db)
        payment_data = PaymentCreate(
            order_identifier=entities.get("order_id", ""),
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
