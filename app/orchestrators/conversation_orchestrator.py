"""
Conversation Orchestrator for managing multi-turn conversations.

This orchestrator is platform-agnostic and handles all conversation state
management and multi-turn workflows. Can be used with Telegram, WhatsApp,
or any other messaging platform.
"""

from typing import Dict, Any, Optional, Tuple
from datetime import date
from uuid import UUID
import logging
import re

from app.services import ConversationService, ConversationState, LLMService
from app.helpers import DisambiguationHelper, TelegramFormatter
from app.orchestrators.order_orchestrator import OrderOrchestrator

logger = logging.getLogger(__name__)


class ConversationOrchestrator:
    """
    Platform-agnostic orchestrator for conversation management.
    
    Handles all multi-turn conversation logic including:
    - Customer phone collection
    - Delivery date collection
    - Customer disambiguation
    - Recipe disambiguation
    
    This is completely independent of the messaging platform.
    """
    
    def __init__(self, conversation_service: ConversationService, llm_service: LLMService):
        """
        Initialize conversation orchestrator.
        
        Args:
            conversation_service: Service for managing conversation state
            llm_service: Service for LLM operations (date parsing, etc.)
        """
        self.conversation_service = conversation_service
        self.llm_service = llm_service
    
    async def handle_customer_phone_input(
        self,
        db,
        tenant_id: UUID,
        chat_id: str,
        text: str,
        conv_context
    ) -> Tuple[bool, str, Optional[Any], Optional[list]]:
        """
        Handle phone number input for customer creation.
        
        Args:
            db: Database session
            tenant_id: Tenant UUID
            chat_id: Chat identifier
            text: User's input text
            conv_context: Current conversation context
        
        Returns:
            Tuple of (success, message, order, items)
            - success: Whether operation succeeded
            - message: Response message
            - order: Created order object (if successful)
            - items: Order items list (if successful)
        """
        # Extract phone number
        phone = OrderOrchestrator.extract_phone_number(text)
        
        if not phone:
            return False, "Please provide a valid phone number (at least 10 digits).", None, None
        
        # Get stored customer name
        customer_name = conv_context.context_data.get('customer_name', '')
        
        if not customer_name:
            self.conversation_service.reset_context(chat_id)
            return False, "Sorry, I lost track of the conversation. Please start over.", None, None
        
        try:
            # Create customer
            customer = OrderOrchestrator.create_customer(db, tenant_id, customer_name, phone)
            
            # Get order data and update with phone
            order_data_dict = conv_context.context_data.get('order_data', {})
            order_data_dict['customer_identifier'] = phone
            
            # Create order
            order, order_items = OrderOrchestrator.create_order_from_context(db, tenant_id, order_data_dict)
            
            # Reset conversation
            self.conversation_service.reset_context(chat_id)
            
            # Format response
            items = OrderOrchestrator.reconstruct_order_items(order_data_dict.get('items', []))
            missing_recipes = OrderOrchestrator.get_missing_recipes(order)
            
            message = TelegramFormatter.format_customer_added_and_order_created(
                customer, order, items, missing_recipes
            )
            
            return True, message, order, items
            
        except ValueError as e:
            self.conversation_service.reset_context(chat_id)
            return False, f"❌ Error: {str(e)}\n\nPlease start over.", None, None
    
    async def handle_delivery_date_input(
        self,
        db,
        tenant_id: UUID,
        chat_id: str,
        text: str,
        conv_context
    ) -> Tuple[bool, str, Optional[Any], Optional[list]]:
        """
        Handle delivery date input for order creation.
        
        Args:
            db: Database session
            tenant_id: Tenant UUID
            chat_id: Chat identifier
            text: User's input text
            conv_context: Current conversation context
        
        Returns:
            Tuple of (success, message, order, items)
        """
        try:
            # Parse date
            date_match = re.search(r'(\d{4})-(\d{2})-(\d{2})', text)
            
            if date_match:
                delivery_date = date.fromisoformat(date_match.group(0))
            else:
                # Use LLM to parse natural language date
                intent_result = await self.llm_service.detect_intent(f"Order for delivery on {text}")
                delivery_date_str = intent_result.entities.get('delivery_date')
                
                if not delivery_date_str:
                    return False, "I couldn't understand the date. Please provide it in a clear format like 'April 20' or '2026-04-20'.", None, None
                
                delivery_date = date.fromisoformat(delivery_date_str)
            
            # Validate date
            is_valid, error_msg = OrderOrchestrator.validate_delivery_date(delivery_date)
            if not is_valid:
                return False, f"❌ {error_msg}", None, None
            
            # Get order data and update with date
            order_data_dict = conv_context.context_data.get('order_data', {})
            order_data_dict['delivery_date'] = delivery_date.isoformat()
            
            logger.info(f"Delivery date handler - parsed {len(order_data_dict.get('items', []))} items")
            
            try:
                # Create order
                order, order_items = OrderOrchestrator.create_order_from_context(db, tenant_id, order_data_dict)
                
                # Reset conversation
                self.conversation_service.reset_context(chat_id)
                
                # Format response
                items = OrderOrchestrator.reconstruct_order_items(order_data_dict.get('items', []))
                missing_recipes = OrderOrchestrator.get_missing_recipes(order)
                
                message = TelegramFormatter.format_order_created(order, items, missing_recipes=missing_recipes)
                
                return True, message, order, items
                
            except ValueError as order_error:
                error_msg = str(order_error)
                
                if "No customer found" in error_msg:
                    customer_name = order_data_dict['customer_identifier']
                    
                    # Transition to phone collection
                    self.conversation_service.set_state(
                        chat_id=chat_id,
                        state=ConversationState.AWAITING_CUSTOMER_PHONE,
                        pending_action="create_order",
                        context_data={
                            'customer_name': customer_name,
                            'order_data': order_data_dict
                        }
                    )
                    
                    items_desc = ", ".join([f"{item.get('quantity', 1)} {item.get('recipe_name', '')}" 
                                           for item in order_data_dict.get('items', [])])
                    message = TelegramFormatter.format_customer_not_found_with_phone_request(customer_name, items_desc)
                    
                    return False, message, None, None
                    
                elif "Multiple customers match" in error_msg:
                    customer_name = order_data_dict['customer_identifier']
                    customers = OrderOrchestrator.find_customers(db, tenant_id, customer_name)
                    
                    customer_options = DisambiguationHelper.create_customer_options(customers)
                    
                    # Transition to customer disambiguation
                    self.conversation_service.set_state(
                        chat_id=chat_id,
                        state=ConversationState.AWAITING_CUSTOMER_DISAMBIGUATION,
                        pending_action="create_order",
                        context_data={
                            'customer_options': customer_options,
                            'order_data': order_data_dict
                        }
                    )
                    
                    return False, DisambiguationHelper.create_customer_prompt(customer_name, customers), None, None
                else:
                    self.conversation_service.reset_context(chat_id)
                    return False, f"❌ Error: {error_msg}\n\nPlease start over.", None, None
            
        except Exception as e:
            logger.error(f"Error parsing delivery date: {e}")
            return False, "I couldn't understand the date. Please provide it in a clear format like 'April 20' or '2026-04-20'.", None, None
    
    async def handle_customer_disambiguation(
        self,
        db,
        tenant_id: UUID,
        chat_id: str,
        text: str,
        conv_context
    ) -> Tuple[bool, str, Optional[Any], Optional[list]]:
        """
        Handle customer selection from multiple matches.
        
        Args:
            db: Database session
            tenant_id: Tenant UUID
            chat_id: Chat identifier
            text: User's input text
            conv_context: Current conversation context
        
        Returns:
            Tuple of (success, message, order, items)
            For cancel_order and delete_order: order and items will be None
            For create_order: may return order and items if complete, or None if needs delivery date
        """
        from app.services import OrderService
        from decimal import Decimal
        from dataclasses import dataclass
        
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
        
        customer_options = conv_context.context_data.get('customer_options', [])
        pending_action = conv_context.pending_action
        
        if not customer_options:
            self.conversation_service.reset_context(chat_id)
            return False, "Sorry, I lost track of the conversation. Please start over.", None, None
        
        # Parse choice
        selected_customer = DisambiguationHelper.parse_customer_choice(text, customer_options)
        
        if not selected_customer:
            return False, DisambiguationHelper.create_invalid_choice_prompt(customer_options, "customer"), None, None
        
        # Handle based on pending action
        if pending_action == "cancel_order":
            # For cancel/delete, return special marker to indicate caller should handle
            # We return the selected customer info in the message as a dict
            return True, {
                'action': 'cancel_order',
                'customer_phone': selected_customer['phone'],
                'delivery_date': conv_context.context_data.get('delivery_date')
            }, None, None
        
        elif pending_action == "delete_order":
            return True, {
                'action': 'delete_order',
                'customer_phone': selected_customer['phone'],
                'delivery_date': conv_context.context_data.get('delivery_date')
            }, None, None
        
        elif pending_action == "create_order":
            # Resume order creation with selected customer
            order_data_dict = conv_context.context_data.get('order_data', {})
            order_data_dict['customer_identifier'] = selected_customer['phone']
            
            # Check if we have delivery_date
            if not order_data_dict.get('delivery_date'):
                # Need to ask for delivery date now
                self.conversation_service.set_state(
                    chat_id=chat_id,
                    state=ConversationState.AWAITING_DELIVERY_DATE,
                    pending_action="create_order",
                    context_data={'order_data': order_data_dict}
                )
                
                items_desc = ", ".join([f"{item.get('quantity', 1)} {item.get('recipe_name', '')}" 
                                       for item in order_data_dict.get('items', [])])
                message = (
                    f"✅ Selected: *{selected_customer['name']}* ({selected_customer['phone']})\n\n"
                    f"📅 When should this order be delivered?\n\n"
                    f"*Items:* {items_desc}\n\n"
                    f"Please provide the delivery date.\n"
                    f"Example: \"tomorrow\", \"April 20\", or \"2026-04-20\""
                )
                return False, message, None, None
            
            # We have all data, create order
            try:
                order, order_items = OrderOrchestrator.create_order_from_context(db, tenant_id, order_data_dict)
                
                # Reset conversation
                self.conversation_service.reset_context(chat_id)
                
                # Format response
                items = OrderOrchestrator.reconstruct_order_items(order_data_dict.get('items', []))
                missing_recipes = OrderOrchestrator.get_missing_recipes(order)
                
                message = f"✅ Order created for *{selected_customer['name']}* ({selected_customer['phone']})!\n\n*Delivery Date:* {order.delivery_date}\n*Status:* {order.status}\n\n*Items:*\n"
                for item in items:
                    message += f"• {item.recipe_name} x{item.quantity} @ ₹{item.selling_price}\n"
                
                if missing_recipes:
                    message += f"\n⚠️ *Warning:* The following recipes don't exist:\n"
                    for recipe_name in missing_recipes:
                        message += f"• {recipe_name}\n"
                    message += f"\n💡 Add the recipe for better tracking!"
                
                return True, message, order, items
                
            except ValueError as e:
                self.conversation_service.reset_context(chat_id)
                return False, f"❌ Error: {str(e)}\n\nPlease start over.", None, None
        
        else:
            self.conversation_service.reset_context(chat_id)
            return False, "Sorry, I don't know how to handle that action. Please start over.", None, None
    
    async def handle_recipe_disambiguation(
        self,
        db,
        tenant_id: UUID,
        chat_id: str,
        text: str,
        conv_context
    ) -> Tuple[bool, str, Optional[Any], Optional[list]]:
        """
        Handle recipe selection from multiple matches.
        
        Args:
            db: Database session
            tenant_id: Tenant UUID
            chat_id: Chat identifier
            text: User's input text
            conv_context: Current conversation context
        
        Returns:
            Tuple of (success, message, order, items)
        """
        recipe_options = conv_context.context_data.get('recipe_options', [])
        
        if not recipe_options:
            self.conversation_service.reset_context(chat_id)
            return False, "Sorry, I lost track of the conversation. Please start over.", None, None
        
        # Parse choice
        selected_recipe = DisambiguationHelper.parse_recipe_choice(text, recipe_options)
        
        if not selected_recipe:
            return False, DisambiguationHelper.create_invalid_choice_prompt(recipe_options, "recipe"), None, None
        
        # Get order data and update recipe name
        order_data_dict = conv_context.context_data.get('order_data', {})
        item_index = conv_context.context_data.get('item_index', 0)
        
        if 'items' in order_data_dict and item_index < len(order_data_dict['items']):
            order_data_dict['items'][item_index]['recipe_name'] = selected_recipe['name']
        
        try:
            # Create order
            order, order_items = OrderOrchestrator.create_order_from_context(db, tenant_id, order_data_dict)
            
            # Reset conversation
            self.conversation_service.reset_context(chat_id)
            
            # Format response
            items = OrderOrchestrator.reconstruct_order_items(order_data_dict.get('items', []))
            message = f"✅ Order created with *{selected_recipe['name']}*!\n\n*Delivery Date:* {order.delivery_date}\n*Status:* {order.status}\n\n*Items:*\n"
            for item in items:
                message += f"• {item.recipe_name} x{item.quantity} @ ₹{item.selling_price}\n"
            
            return True, message, order, items
            
        except ValueError as e:
            self.conversation_service.reset_context(chat_id)
            return False, f"❌ Error: {str(e)}\n\nPlease start over.", None, None
