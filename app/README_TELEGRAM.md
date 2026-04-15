# Telegram Bot Integration

This module provides integration with the Telegram Bot API for the Bakery Operations Bot.

## Overview

The `TelegramClient` class wraps the `python-telegram-bot` library to provide:
- Message sending with Markdown/HTML formatting
- Webhook configuration and management
- Error handling with proper logging

## Configuration

Set the following environment variables in your `.env` file:

```env
TELEGRAM_BOT_TOKEN=your-telegram-bot-token-here
WEBHOOK_URL=https://your-domain.com/webhook
```

## Usage

### Initialize the Client

```python
from app.telegram import TelegramClient

# Uses TELEGRAM_BOT_TOKEN from settings
client = TelegramClient()

# Or provide token explicitly
client = TelegramClient(bot_token="your-token")
```

### Send Messages

```python
# Send a simple message
await client.send_message(
    chat_id="123456789",
    text="Hello from Bakery Bot!"
)

# Send with Markdown formatting
await client.send_message(
    chat_id="123456789",
    text="*Bold* _italic_ `code`",
    parse_mode="Markdown"
)

# Send with HTML formatting
await client.send_message(
    chat_id="123456789",
    text="<b>Bold</b> <i>italic</i>",
    parse_mode="HTML"
)
```

### Configure Webhook

```python
# Set webhook URL
await client.set_webhook(webhook_url="https://example.com/webhook")

# Or use WEBHOOK_URL from settings
await client.set_webhook()

# Get webhook information
info = await client.get_webhook_info()
print(f"Webhook URL: {info['url']}")
print(f"Pending updates: {info['pending_update_count']}")

# Delete webhook
await client.delete_webhook()
```

## Error Handling

All methods raise `TelegramError` on failure. Always wrap calls in try-except blocks:

```python
from telegram.error import TelegramError

try:
    await client.send_message(chat_id="123", text="Hello")
except TelegramError as e:
    logger.error(f"Failed to send message: {e}")
    # Handle error appropriately
```

## Requirements Validation

This implementation satisfies:
- **Requirement 1.5**: Bot sends welcome messages and responses to owners
- **Requirement 23.1**: Error handling with user-friendly messages (via TelegramError handling)

## Testing

Run the test suite:

```bash
pytest tests/test_telegram.py -v
```

All tests use mocked Bot instances to avoid requiring actual Telegram API credentials.

## Examples

See `app/telegram_example.py` for complete usage examples.
