"""
Unit tests for Telegram Bot API integration.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from telegram.error import TelegramError

from app.telegram import TelegramClient


class TestTelegramClient:
    """Test suite for TelegramClient class."""

    @pytest.fixture
    def mock_bot(self):
        """Create a mock Bot instance."""
        with patch("app.telegram.Bot") as mock_bot_class:
            mock_bot_instance = MagicMock()
            mock_bot_class.return_value = mock_bot_instance
            yield mock_bot_instance

    @pytest.fixture
    def client(self, mock_bot):
        """Create a TelegramClient instance with mocked Bot."""
        return TelegramClient(bot_token="test-token-123")

    def test_init_with_token(self, mock_bot):
        """Test TelegramClient initialization with provided token."""
        client = TelegramClient(bot_token="custom-token")
        assert client.bot_token == "custom-token"
        assert client.bot is not None

    def test_init_without_token_raises_error(self):
        """Test TelegramClient initialization without token raises ValueError."""
        with patch("app.telegram.settings") as mock_settings:
            mock_settings.TELEGRAM_BOT_TOKEN = ""
            with pytest.raises(ValueError, match="Telegram bot token is required"):
                TelegramClient()

    @pytest.mark.asyncio
    async def test_send_message_success(self, client, mock_bot):
        """Test successful message sending."""
        mock_bot.send_message = AsyncMock(return_value=True)
        
        result = await client.send_message(
            chat_id="123456",
            text="Test message"
        )
        
        assert result is True
        mock_bot.send_message.assert_called_once_with(
            chat_id="123456",
            text="Test message",
            parse_mode="Markdown"
        )

    @pytest.mark.asyncio
    async def test_send_message_with_custom_parse_mode(self, client, mock_bot):
        """Test message sending with custom parse mode."""
        mock_bot.send_message = AsyncMock(return_value=True)
        
        result = await client.send_message(
            chat_id="123456",
            text="<b>HTML message</b>",
            parse_mode="HTML"
        )
        
        assert result is True
        mock_bot.send_message.assert_called_once_with(
            chat_id="123456",
            text="<b>HTML message</b>",
            parse_mode="HTML"
        )

    @pytest.mark.asyncio
    async def test_send_message_failure(self, client, mock_bot):
        """Test message sending failure raises TelegramError."""
        mock_bot.send_message = AsyncMock(
            side_effect=TelegramError("Network error")
        )
        
        with pytest.raises(TelegramError, match="Network error"):
            await client.send_message(
                chat_id="123456",
                text="Test message"
            )

    @pytest.mark.asyncio
    async def test_set_webhook_success(self, client, mock_bot):
        """Test successful webhook registration."""
        mock_bot.set_webhook = AsyncMock(return_value=True)
        
        result = await client.set_webhook(
            webhook_url="https://example.com/webhook"
        )
        
        assert result is True
        mock_bot.set_webhook.assert_called_once_with(
            url="https://example.com/webhook"
        )

    @pytest.mark.asyncio
    async def test_set_webhook_without_url_raises_error(self, client):
        """Test webhook registration without URL raises ValueError."""
        with patch("app.telegram.settings") as mock_settings:
            mock_settings.WEBHOOK_URL = ""
            with pytest.raises(ValueError, match="Webhook URL is required"):
                await client.set_webhook()

    @pytest.mark.asyncio
    async def test_set_webhook_failure(self, client, mock_bot):
        """Test webhook registration failure raises TelegramError."""
        mock_bot.set_webhook = AsyncMock(
            side_effect=TelegramError("Invalid URL")
        )
        
        with pytest.raises(TelegramError, match="Invalid URL"):
            await client.set_webhook(
                webhook_url="https://example.com/webhook"
            )

    @pytest.mark.asyncio
    async def test_delete_webhook_success(self, client, mock_bot):
        """Test successful webhook deletion."""
        mock_bot.delete_webhook = AsyncMock(return_value=True)
        
        result = await client.delete_webhook()
        
        assert result is True
        mock_bot.delete_webhook.assert_called_once()

    @pytest.mark.asyncio
    async def test_delete_webhook_failure(self, client, mock_bot):
        """Test webhook deletion failure raises TelegramError."""
        mock_bot.delete_webhook = AsyncMock(
            side_effect=TelegramError("API error")
        )
        
        with pytest.raises(TelegramError, match="API error"):
            await client.delete_webhook()

    @pytest.mark.asyncio
    async def test_get_webhook_info_success(self, client, mock_bot):
        """Test successful webhook info retrieval."""
        mock_info = MagicMock()
        mock_info.url = "https://example.com/webhook"
        mock_info.has_custom_certificate = False
        mock_info.pending_update_count = 0
        mock_info.last_error_date = None
        mock_info.last_error_message = None
        mock_info.max_connections = 40
        mock_info.allowed_updates = None
        
        mock_bot.get_webhook_info = AsyncMock(return_value=mock_info)
        
        result = await client.get_webhook_info()
        
        assert result["url"] == "https://example.com/webhook"
        assert result["has_custom_certificate"] is False
        assert result["pending_update_count"] == 0
        mock_bot.get_webhook_info.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_webhook_info_failure(self, client, mock_bot):
        """Test webhook info retrieval failure raises TelegramError."""
        mock_bot.get_webhook_info = AsyncMock(
            side_effect=TelegramError("API error")
        )
        
        with pytest.raises(TelegramError, match="API error"):
            await client.get_webhook_info()
