"""
Telegram Bot API integration module.

This module provides a wrapper around python-telegram-bot for sending messages
and managing webhook configuration.
"""

import logging
from typing import Optional

from telegram import Bot
from telegram.error import TelegramError

from app.config import settings

logger = logging.getLogger(__name__)


class TelegramClient:
    """
    Wrapper class for Telegram Bot API operations.
    
    Provides methods for sending messages and configuring webhooks.
    """

    def __init__(self, bot_token: Optional[str] = None):
        """
        Initialize the Telegram client.
        
        Args:
            bot_token: Telegram bot token. If not provided, uses TELEGRAM_BOT_TOKEN from settings.
        """
        self.bot_token = bot_token or settings.TELEGRAM_BOT_TOKEN
        if not self.bot_token:
            raise ValueError("Telegram bot token is required")
        
        self.bot = Bot(token=self.bot_token)

    async def send_message(
        self,
        chat_id: str,
        text: str,
        parse_mode: Optional[str] = "Markdown"
    ) -> bool:
        """
        Send a message to a Telegram chat.
        
        Args:
            chat_id: Telegram chat ID to send the message to
            text: Message text to send
            parse_mode: Message formatting mode (Markdown, HTML, or None)
        
        Returns:
            True if message was sent successfully, False otherwise
        
        Raises:
            TelegramError: If there's an error sending the message
        """
        try:
            await self.bot.send_message(
                chat_id=chat_id,
                text=text,
                parse_mode=parse_mode
            )
            logger.info(f"Message sent successfully to chat_id: {chat_id}")
            return True
        except TelegramError as e:
            logger.error(f"Failed to send message to chat_id {chat_id}: {e}")
            raise

    async def set_webhook(self, webhook_url: Optional[str] = None) -> bool:
        """
        Register or update the webhook URL for receiving Telegram updates.
        
        Args:
            webhook_url: The HTTPS URL to receive webhook updates.
                        If not provided, uses WEBHOOK_URL from settings.
        
        Returns:
            True if webhook was set successfully, False otherwise
        
        Raises:
            TelegramError: If there's an error setting the webhook
            ValueError: If webhook_url is not provided and not in settings
        """
        url = webhook_url or settings.WEBHOOK_URL
        if not url:
            raise ValueError("Webhook URL is required")
        
        try:
            result = await self.bot.set_webhook(url=url)
            if result:
                logger.info(f"Webhook set successfully to: {url}")
            else:
                logger.warning(f"Failed to set webhook to: {url}")
            return result
        except TelegramError as e:
            logger.error(f"Error setting webhook to {url}: {e}")
            raise

    async def delete_webhook(self) -> bool:
        """
        Remove the webhook configuration.
        
        Returns:
            True if webhook was deleted successfully, False otherwise
        
        Raises:
            TelegramError: If there's an error deleting the webhook
        """
        try:
            result = await self.bot.delete_webhook()
            if result:
                logger.info("Webhook deleted successfully")
            else:
                logger.warning("Failed to delete webhook")
            return result
        except TelegramError as e:
            logger.error(f"Error deleting webhook: {e}")
            raise

    async def get_webhook_info(self) -> dict:
        """
        Get current webhook status and configuration.
        
        Returns:
            Dictionary containing webhook information
        
        Raises:
            TelegramError: If there's an error getting webhook info
        """
        try:
            info = await self.bot.get_webhook_info()
            webhook_data = {
                "url": info.url,
                "has_custom_certificate": info.has_custom_certificate,
                "pending_update_count": info.pending_update_count,
                "last_error_date": info.last_error_date,
                "last_error_message": info.last_error_message,
                "max_connections": info.max_connections,
                "allowed_updates": info.allowed_updates,
            }
            logger.info(f"Webhook info retrieved: {webhook_data}")
            return webhook_data
        except TelegramError as e:
            logger.error(f"Error getting webhook info: {e}")
            raise
