"""
Telegram Bot Polling Listener.

This module implements a polling-based listener for Telegram messages,
allowing local testing without requiring a public webhook URL.

This is a thin adapter that:
- Receives Telegram messages
- Calls orchestrators and handlers for business logic
- Sends Telegram responses

All business logic is in platform-agnostic handlers and orchestrators.
"""

import asyncio
import logging
from typing import Optional
from uuid import UUID

from telegram import Update
from telegram.ext import Application, MessageHandler, filters, ContextTypes

from app.database import get_db
from app.services import (
    TenantService,
    LLMService,
    Intent,
    IntentResult,
    ConversationService,
    ConversationState
)
from app.orchestrators import ConversationOrchestrator
from app.handlers import RequestHandler
from app.error_handler import ErrorHandler, format_error_for_telegram
from app.config import settings

logger = logging.getLogger(__name__)


class TelegramBotListener:
    """
    Telegram bot listener using polling mode.
    
    Polls Telegram for new messages and processes them through the
    service layer with LLM-based intent detection.
    
    This is a thin wrapper around the business logic orchestrators.
    The actual business logic is in platform-agnostic orchestrators
    that can be reused with WhatsApp, Slack, or any other platform.
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
        self.conversation_orchestrator = ConversationOrchestrator(
            self.conversation_service,
            self.llm_service
        )
        self.request_handler = RequestHandler(self.conversation_service)
        self.application = None
        
        # Intent routing map - reduces cyclomatic complexity
        self._intent_handlers = self._build_intent_handlers()
    
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
                    logger.info(f"Entities extracted: {intent_result.entities}")
                    
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
    
    def _build_intent_handlers(self):
        """
        Build intent routing map to reduce cyclomatic complexity.
        
        Returns:
            dict: Mapping of Intent to handler function
        """
        return {
            # Customer operations
            Intent.CREATE_CUSTOMER: lambda db, tid, e, cid: self.request_handler.handle_create_customer(db, tid, e),
            Intent.GET_CUSTOMER: lambda db, tid, e, cid: self.request_handler.handle_get_customer(db, tid, e),
            Intent.LIST_CUSTOMERS: lambda db, tid, e, cid: self.request_handler.handle_list_customers(db, tid),
            
            # Inventory operations
            Intent.ADD_INVENTORY: lambda db, tid, e, cid: self.request_handler.handle_add_inventory(db, tid, e),
            Intent.UPDATE_INVENTORY: lambda db, tid, e, cid: self.request_handler.handle_update_inventory(db, tid, e),
            Intent.CHECK_STOCK: lambda db, tid, e, cid: self.request_handler.handle_check_stock(db, tid, e),
            Intent.LIST_INVENTORY: lambda db, tid, e, cid: self.request_handler.handle_list_inventory(db, tid, e),
            
            # Recipe operations
            Intent.CREATE_RECIPE: lambda db, tid, e, cid: self.request_handler.handle_create_recipe(db, tid, e),
            Intent.ADD_RECIPE_COMPONENT: lambda db, tid, e, cid: self.request_handler.handle_add_recipe_component(db, tid, e),
            Intent.CALCULATE_RECIPE_COST: lambda db, tid, e, cid: self.request_handler.handle_calculate_recipe_cost(db, tid, e),
            
            # Order operations
            Intent.CREATE_ORDER: lambda db, tid, e, cid: self.request_handler.handle_create_order(db, tid, e, cid),
            Intent.CANCEL_ORDER: lambda db, tid, e, cid: self.request_handler.handle_cancel_order(db, tid, e, cid),
            Intent.DELETE_ORDER: lambda db, tid, e, cid: self.request_handler.handle_delete_order(db, tid, e, cid),
            Intent.MARK_DELIVERED: lambda db, tid, e, cid: self.request_handler.handle_mark_delivered(db, tid, e),
            Intent.UPCOMING_ORDERS: lambda db, tid, e, cid: self.request_handler.handle_upcoming_orders(db, tid, e),
            Intent.UNPAID_ORDERS: lambda db, tid, e, cid: self.request_handler.handle_unpaid_orders(db, tid),
            
            # Payment operations
            Intent.RECORD_PAYMENT: lambda db, tid, e, cid: self.request_handler.handle_record_payment(db, tid, e),
            Intent.PAYMENT_HISTORY: lambda db, tid, e, cid: self.request_handler.handle_payment_history(db, tid, e),
            
            # Reporting operations
            Intent.WEEKLY_PROFIT: lambda db, tid, e, cid: self.request_handler.handle_weekly_profit(db, tid),
        }
    
    async def route_intent(self, db, tenant_id: UUID, chat_id: str, intent_result: IntentResult) -> str:
        """
        Route intent to appropriate handler using dispatch map.
        
        Cyclomatic complexity: 1 (reduced from ~20)
        
        Args:
            db: Database session
            tenant_id: UUID of the tenant
            chat_id: Chat identifier
            intent_result: Detected intent and entities
        
        Returns:
            str: Formatted response message for Telegram
        """
        intent = intent_result.intent
        entities = intent_result.entities
        
        try:
            handler = self._intent_handlers.get(intent)
            if handler:
                return await handler(db, tenant_id, entities, chat_id)
            else:
                return "I'm not sure how to help with that. Could you please rephrase your request?"
        
        except Exception as e:
            logger.error(f"Error handling intent {intent}: {e}", exc_info=True)
            error_response = ErrorHandler.handle_exception(e)
            return format_error_for_telegram(error_response)
    
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
        Delegates to ConversationOrchestrator.
        """
        success, message, order, items = await self.conversation_orchestrator.handle_customer_phone_input(
            db, tenant_id, chat_id, text, conv_context
        )
        return message
    
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
        Delegates to ConversationOrchestrator.
        """
        success, message, order, items = await self.conversation_orchestrator.handle_delivery_date_input(
            db, tenant_id, chat_id, text, conv_context
        )
        return message
    
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
        Delegates to ConversationOrchestrator.
        """
        success, result, order, items = await self.conversation_orchestrator.handle_customer_disambiguation(
            db, tenant_id, chat_id, text, conv_context
        )
        
        # Check if result is a dict (indicating we need to handle cancel/delete)
        if isinstance(result, dict):
            action = result.get('action')
            if action == 'cancel_order':
                self.conversation_service.reset_context(chat_id)
                entities = {
                    'customer_identifier': result['customer_phone'],
                    'delivery_date': result['delivery_date']
                }
                return await self.handle_cancel_order(db, tenant_id, entities, chat_id)
            elif action == 'delete_order':
                self.conversation_service.reset_context(chat_id)
                entities = {
                    'customer_identifier': result['customer_phone'],
                    'delivery_date': result['delivery_date']
                }
                return await self.handle_delete_order(db, tenant_id, entities, chat_id)
        
        # Otherwise, result is a message string
        return result
    
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
        Delegates to ConversationOrchestrator.
        """
        success, message, order, items = await self.conversation_orchestrator.handle_recipe_disambiguation(
            db, tenant_id, chat_id, text, conv_context
        )
        return message
    
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
