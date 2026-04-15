"""
Example usage of TelegramClient.

This module demonstrates how to use the TelegramClient for common operations.
"""

import asyncio
from app.telegram import TelegramClient
from app.config import settings


async def example_send_message():
    """Example: Send a message to a chat."""
    client = TelegramClient()
    
    try:
        await client.send_message(
            chat_id="123456789",
            text="Hello from Bakery Operations Bot! 🍰"
        )
        print("Message sent successfully")
    except Exception as e:
        print(f"Error sending message: {e}")


async def example_setup_webhook():
    """Example: Set up webhook for receiving updates."""
    client = TelegramClient()
    
    try:
        # Set webhook
        success = await client.set_webhook()
        if success:
            print(f"Webhook configured: {settings.WEBHOOK_URL}")
        
        # Get webhook info
        info = await client.get_webhook_info()
        print(f"Webhook status: {info}")
        
    except Exception as e:
        print(f"Error setting up webhook: {e}")


async def example_send_formatted_message():
    """Example: Send a formatted message with Markdown."""
    client = TelegramClient()
    
    message = """
*Welcome to Bakery Operations Bot!*

You can now:
• Add customers
• Track inventory
• Manage recipes
• Create orders
• Record payments

Type your request in natural language to get started.
    """
    
    try:
        await client.send_message(
            chat_id="123456789",
            text=message.strip(),
            parse_mode="Markdown"
        )
        print("Formatted message sent successfully")
    except Exception as e:
        print(f"Error sending formatted message: {e}")


if __name__ == "__main__":
    # Run examples
    print("TelegramClient Examples")
    print("=" * 50)
    
    # Note: These examples require a valid TELEGRAM_BOT_TOKEN
    # and chat_id to work. Update .env file with your credentials.
    
    # Uncomment to run examples:
    # asyncio.run(example_send_message())
    # asyncio.run(example_setup_webhook())
    # asyncio.run(example_send_formatted_message())
    
    print("\nExamples are commented out. Update credentials and uncomment to run.")
