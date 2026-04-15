from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import JSONResponse
import logging
from datetime import datetime, date
from decimal import Decimal
from uuid import UUID
from dataclasses import dataclass

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
from app.telegram_client import TelegramClient
from app.error_handler import ErrorHandler, format_error_for_telegram

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

app = FastAPI(
    title="Bakery Operations Bot",
    description="Telegram bot backend for home bakery operations management",
    version="0.1.0",
)

# Initialize clients
telegram_client = TelegramClient()
llm_service = LLMService()


@app.get("/health")
async def health_check():
    return JSONResponse(content={"status": "ok"})


@app.post("/webhook")
async def webhook(request: Request):
    """
    Telegram webhook handler.
    
    Receives messages from Telegram, detects intent using LLM,
    routes to appropriate service, and sends response back to user.
    
    Requirements:
        - 1.5: Handle Telegram webhook messages
        - 17.1: Parse Telegram webhook payload
        - 17.2: Implement message routing logic
        - 17.3: Implement intent-to-service mapping
        - 17.4: Format responses for Telegram
        - 23.1: Handle errors and return user-friendly messages
    """
    try:
        # Parse Telegram webhook payload
        payload = await request.json()
        logger.info(f"Received webhook payload: {payload}")
        
        # Extract message data
        message = payload.get("message", {})
        if not message:
            logger.warning("No message in webhook payload")
            return JSONResponse(content={"ok": True})
        
        chat_id = str(message.get("chat", {}).get("id", ""))
        text = message.get("text", "").strip()
        
        if not chat_id or not text:
            logger.warning(f"Missing chat_id or text: chat_id={chat_id}, text={text}")
            return JSONResponse(content={"ok": True})
        
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
            intent_result = await llm_service.detect_intent(text)
            logger.info(f"Intent detected: {intent_result.intent} (confidence: {intent_result.confidence})")
            
            # Check confidence
            if not llm_service.is_confident(intent_result):
                clarification = llm_service.get_clarification_message(intent_result)
                await telegram_client.send_message(chat_id, clarification)
                return JSONResponse(content={"ok": True})
            
            # Route to appropriate service based on intent
            response_text = await route_intent(
                db=db,
                tenant_id=tenant_id,
                intent_result=intent_result
            )
            
            # Send response to user
            await telegram_client.send_message(chat_id, response_text)
            
        finally:
            db.close()
        
        return JSONResponse(content={"ok": True})
    
    except Exception as e:
        logger.error(f"Error processing webhook: {e}", exc_info=True)
        
        # Try to send error message to user
        try:
            if chat_id:
                error_response = ErrorHandler.handle_exception(e)
                error_message = format_error_for_telegram(error_response)
                await telegram_client.send_message(chat_id, error_message)
        except Exception as send_error:
            logger.error(f"Failed to send error message: {send_error}")
        
        return JSONResponse(content={"ok": True})


async def route_intent(db, tenant_id: UUID, intent_result: IntentResult) -> str:
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
            return await handle_create_customer(db, tenant_id, entities)
        elif intent == Intent.GET_CUSTOMER:
            return await handle_get_customer(db, tenant_id, entities)
        elif intent == Intent.LIST_CUSTOMERS:
            return await handle_list_customers(db, tenant_id)
        
        # Inventory operations
        elif intent == Intent.ADD_INVENTORY:
            return await handle_add_inventory(db, tenant_id, entities)
        elif intent == Intent.UPDATE_INVENTORY:
            return await handle_update_inventory(db, tenant_id, entities)
        elif intent == Intent.CHECK_STOCK:
            return await handle_check_stock(db, tenant_id, entities)
        elif intent == Intent.LIST_INVENTORY:
            return await handle_list_inventory(db, tenant_id)
        
        # Recipe operations
        elif intent == Intent.CREATE_RECIPE:
            return await handle_create_recipe(db, tenant_id, entities)
        elif intent == Intent.ADD_RECIPE_COMPONENT:
            return await handle_add_recipe_component(db, tenant_id, entities)
        elif intent == Intent.CALCULATE_RECIPE_COST:
            return await handle_calculate_recipe_cost(db, tenant_id, entities)
        
        # Order operations
        elif intent == Intent.CREATE_ORDER:
            return await handle_create_order(db, tenant_id, entities)
        elif intent == Intent.MARK_DELIVERED:
            return await handle_mark_delivered(db, tenant_id, entities)
        elif intent == Intent.UPCOMING_ORDERS:
            return await handle_upcoming_orders(db, tenant_id)
        elif intent == Intent.UNPAID_ORDERS:
            return await handle_unpaid_orders(db, tenant_id)
        
        # Payment operations
        elif intent == Intent.RECORD_PAYMENT:
            return await handle_record_payment(db, tenant_id, entities)
        elif intent == Intent.PAYMENT_HISTORY:
            return await handle_payment_history(db, tenant_id, entities)
        
        # Reporting operations
        elif intent == Intent.WEEKLY_PROFIT:
            return await handle_weekly_profit(db, tenant_id)
        
        else:
            return "I'm not sure how to help with that. Could you please rephrase your request?"
    
    except Exception as e:
        logger.error(f"Error handling intent {intent}: {e}", exc_info=True)
        error_response = ErrorHandler.handle_exception(e)
        return format_error_for_telegram(error_response)


# Customer handlers
async def handle_create_customer(db, tenant_id: UUID, entities: dict) -> str:
    service = CustomerService(db)
    name = entities.get("name", "")
    phone = entities.get("phone", "")
    
    customer = service.create_customer(tenant_id, name, phone)
    return f"✅ Customer created successfully!\n\nName: {customer.name}\nPhone: {customer.phone}"


async def handle_get_customer(db, tenant_id: UUID, entities: dict) -> str:
    service = CustomerService(db)
    search = entities.get("name") or entities.get("phone") or entities.get("customer_identifier", "")
    
    customers = service.get_customer(tenant_id, search)
    
    if len(customers) == 0:
        return f"❌ No customer found matching '{search}'"
    elif len(customers) == 1:
        c = customers[0]
        return f"📋 Customer found:\n\nName: {c.name}\nPhone: {c.phone}"
    else:
        result = f"📋 Found {len(customers)} customers:\n\n"
        for c in customers:
            result += f"• {c.name} ({c.phone})\n"
        return result


async def handle_list_customers(db, tenant_id: UUID) -> str:
    service = CustomerService(db)
    customers = service.list_customers(tenant_id)
    
    if not customers:
        return "📋 No customers found"
    
    result = f"📋 All Customers ({len(customers)}):\n\n"
    for c in customers:
        result += f"• {c.name} - {c.phone}\n"
    return result


# Inventory handlers
async def handle_add_inventory(db, tenant_id: UUID, entities: dict) -> str:
    service = InventoryService(db)
    item = InventoryItemCreate(
        name=entities.get("name", ""),
        category=entities.get("category", ""),
        quantity=Decimal(str(entities.get("quantity", 0))),
        unit=entities.get("unit", ""),
        cost_per_unit=Decimal(str(entities.get("cost_per_unit", 0)))
    )
    
    created_item = service.create_item(tenant_id, item)
    return f"✅ Inventory item added!\n\nName: {created_item.name}\nCategory: {created_item.category}\nQuantity: {created_item.quantity} {created_item.unit}\nCost: ₹{created_item.cost_per_unit}/{created_item.unit}"


async def handle_update_inventory(db, tenant_id: UUID, entities: dict) -> str:
    service = InventoryService(db)
    name = entities.get("name", "")
    updates = {}
    
    if "quantity" in entities:
        updates["quantity"] = entities["quantity"]
    if "cost_per_unit" in entities:
        updates["cost_per_unit"] = entities["cost_per_unit"]
    
    item = service.update_item(tenant_id, name, updates)
    return f"✅ Inventory updated!\n\nName: {item.name}\nQuantity: {item.quantity} {item.unit}\nCost: ₹{item.cost_per_unit}/{item.unit}"


async def handle_check_stock(db, tenant_id: UUID, entities: dict) -> str:
    service = InventoryService(db)
    name = entities.get("name", "")
    
    item = service.get_item(tenant_id, name)
    return f"📦 Stock Level:\n\nItem: {item.name}\nCategory: {item.category}\nQuantity: {item.quantity} {item.unit}\nCost: ₹{item.cost_per_unit}/{item.unit}"


async def handle_list_inventory(db, tenant_id: UUID) -> str:
    service = InventoryService(db)
    items_by_category = service.list_items(tenant_id)
    
    if not items_by_category.get("ingredients") and not items_by_category.get("packaging"):
        return "📦 No inventory items found"
    
    result = "📦 Inventory:\n\n"
    
    if items_by_category.get("ingredients"):
        result += "**Ingredients:**\n"
        for item in items_by_category["ingredients"]:
            result += f"• {item.name}: {item.quantity} {item.unit} @ ₹{item.cost_per_unit}/{item.unit}\n"
        result += "\n"
    
    if items_by_category.get("packaging"):
        result += "**Packaging:**\n"
        for item in items_by_category["packaging"]:
            result += f"• {item.name}: {item.quantity} {item.unit} @ ₹{item.cost_per_unit}/{item.unit}\n"
    
    return result


# Recipe handlers
async def handle_create_recipe(db, tenant_id: UUID, entities: dict) -> str:
    service = RecipeService(db)
    name = entities.get("name", "")
    yield_per_batch = int(entities.get("yield_per_batch", 1))
    
    recipe = service.create_recipe(tenant_id, name, yield_per_batch)
    return f"✅ Recipe created!\n\nName: {recipe.name}\nYield: {recipe.yield_per_batch} units per batch"


async def handle_add_recipe_component(db, tenant_id: UUID, entities: dict) -> str:
    service = RecipeService(db)
    recipe_name = entities.get("recipe_name", "")
    component = RecipeComponentCreate(
        item_name=entities.get("item_name", ""),
        quantity=Decimal(str(entities.get("quantity", 0))),
        component_type=entities.get("component_type", "")
    )
    
    service.add_component(tenant_id, recipe_name, component)
    return f"✅ Component added to recipe '{recipe_name}'!\n\nItem: {component.item_name}\nQuantity: {component.quantity}\nType: {component.component_type}"


async def handle_calculate_recipe_cost(db, tenant_id: UUID, entities: dict) -> str:
    service = RecipeService(db)
    recipe_name = entities.get("recipe_name", "")
    
    cost = service.calculate_cost(tenant_id, recipe_name)
    return f"💰 Recipe Cost for '{recipe_name}':\n\nIngredient Cost: ₹{cost.ingredient_cost:.2f}\nPackaging Cost: ₹{cost.packaging_cost:.2f}\nUnit Cost: ₹{cost.unit_cost:.2f}"


# Order handlers
async def handle_create_order(db, tenant_id: UUID, entities: dict) -> str:
    service = OrderService(db)
    
    # Parse order items
    items = []
    for item_data in entities.get("items", []):
        items.append(OrderItemCreate(
            recipe_name=item_data.get("recipe_name", ""),
            quantity=int(item_data.get("quantity", 1)),
            selling_price=Decimal(str(item_data.get("selling_price", 0)))
        ))
    
    # Parse delivery date
    delivery_date_str = entities.get("delivery_date", "")
    delivery_date = date.fromisoformat(delivery_date_str) if delivery_date_str else date.today()
    
    order_data = OrderCreate(
        customer_identifier=entities.get("customer_identifier", ""),
        delivery_date=delivery_date,
        items=items
    )
    
    order = service.create_order(tenant_id, order_data)
    
    result = f"✅ Order created!\n\nOrder ID: {order.order_id}\nDelivery Date: {order.delivery_date}\nStatus: {order.status}\n\nItems:\n"
    for item in items:
        result += f"• {item.recipe_name} x{item.quantity} @ ₹{item.selling_price}\n"
    
    return result


async def handle_mark_delivered(db, tenant_id: UUID, entities: dict) -> str:
    service = OrderService(db)
    order_id = UUID(entities.get("order_id", ""))
    
    order = service.mark_delivered(tenant_id, order_id)
    return f"✅ Order marked as delivered!\n\nOrder ID: {order.order_id}\nStatus: {order.status}"


async def handle_upcoming_orders(db, tenant_id: UUID) -> str:
    service = OrderService(db)
    orders = service.get_upcoming_orders(tenant_id)
    
    if not orders:
        return "📅 No upcoming orders"
    
    result = f"📅 Upcoming Orders ({len(orders)}):\n\n"
    for order in orders:
        result += f"**{order['customer_name']}** - {order['delivery_date']}\n"
        for item in order['items']:
            result += f"  • {item['recipe_name']} x{item['quantity']}\n"
        result += f"  Total: ₹{order['total_price']}\n\n"
    
    return result


async def handle_unpaid_orders(db, tenant_id: UUID) -> str:
    service = OrderService(db)
    orders = service.get_unpaid_orders(tenant_id)
    
    if not orders:
        return "💰 All orders are paid!"
    
    result = f"💰 Unpaid Orders ({len(orders)}):\n\n"
    for order in orders:
        result += f"**{order['customer_name']}**\n"
        result += f"  Total: ₹{order['total_amount']}\n"
        result += f"  Paid: ₹{order['amount_paid']}\n"
        result += f"  Due: ₹{order['amount_due']}\n\n"
    
    return result


# Payment handlers
async def handle_record_payment(db, tenant_id: UUID, entities: dict) -> str:
    service = PaymentService(db)
    payment_data = PaymentCreate(
        order_identifier=entities.get("order_id", ""),
        amount=Decimal(str(entities.get("amount", 0))),
        method=entities.get("method", "")
    )
    
    payment = service.record_payment(tenant_id, payment_data)
    return f"✅ Payment recorded!\n\nAmount: ₹{payment.amount}\nMethod: {payment.method}"


async def handle_payment_history(db, tenant_id: UUID, entities: dict) -> str:
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
    
    result = f"💳 Payment History ({len(payments)}):\n\n"
    for payment in payments:
        result += f"**{payment['customer_name']}**\n"
        result += f"  Amount: ₹{payment['amount']}\n"
        result += f"  Method: {payment['method']}\n"
        result += f"  Date: {payment['payment_date']}\n\n"
    
    return result


# Reporting handlers
async def handle_weekly_profit(db, tenant_id: UUID) -> str:
    service = ReportingService(db)
    report = service.calculate_weekly_profit(tenant_id)
    
    result = f"📊 Weekly Profit Report\n"
    result += f"Week: {report.week_start} to {report.week_end}\n\n"
    result += f"Revenue: ₹{report.total_revenue:.2f}\n"
    result += f"Ingredient Cost: ₹{report.total_ingredient_cost:.2f}\n"
    result += f"Packaging Cost: ₹{report.total_packaging_cost:.2f}\n"
    result += f"**Gross Profit: ₹{report.gross_profit:.2f}**"
    
    return result

