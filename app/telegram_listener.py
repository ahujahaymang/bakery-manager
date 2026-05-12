"""
Telegram Bot Polling Listener.

Telegram protocol adapter only:
- Receive messages and photos
- Resolve tenant from chat_id
- Delegate to RequestHandler
- Send the response back

No business logic here.
"""

import asyncio
import logging
import threading
from typing import Optional

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, MessageHandler, CallbackQueryHandler, filters, ContextTypes

from app.database import get_registry_db
from app.services.tenant_service import TenantService
from app.handlers.request_handler import RequestHandler
from app.services.admin_notifier import AdminNotifier
from app.error_handler import ErrorHandler
from app.config import settings
from app.services.backup_service import create_backup_service
from app.instagram_listener import InstagramListener

logger = logging.getLogger(__name__)

# How long to wait for text requests (seconds)
REQUEST_TIMEOUT = 60
# How long to wait for image processing — vision + agent planning can be slow for large catalogs
IMAGE_TIMEOUT = 180


class TelegramBotListener:
    """Telegram adapter. Owns nothing except the Telegram connection."""

    def __init__(self, bot_token: Optional[str] = None):
        self.bot_token = bot_token or settings.TELEGRAM_BOT_TOKEN
        if not self.bot_token:
            raise ValueError("TELEGRAM_BOT_TOKEN must be configured")

        self.instagram = InstagramListener(notify_owner_fn=self._send_to_chat)
        self.admin_notifier = AdminNotifier(
            admin_chat_id=settings.ADMIN_CHAT_ID or "",
            # send_fn wired after application starts
        )
        self.handler = RequestHandler(
            admin_notifier=self.admin_notifier,
            instagram_listener=self.instagram,
        )
        self.application = None
        self.backup = create_backup_service()

    def _start_webhook_server(self):
        """Start the FastAPI webhook server in a background thread."""
        from app.webhook_server import app as webhook_app, register_instagram
        register_instagram(self.instagram)

        import uvicorn

        def run():
            uvicorn.run(webhook_app, host="0.0.0.0", port=8000, log_level="warning")

        thread = threading.Thread(target=run, daemon=True)
        thread.start()
        logger.info("Webhook server started on port 8000")

    async def _send_to_chat(self, chat_id: str, message: str):
        """Send a message to a specific chat_id (used by Instagram listener)."""
        try:
            await self.application.bot.send_message(
                chat_id=int(chat_id),
                text=message,
                parse_mode="Markdown"
            )
        except Exception:
            try:
                await self.application.bot.send_message(chat_id=int(chat_id), text=message)
            except Exception as e:
                logger.error(f"Failed to send notification to {chat_id}: {e}")

    async def _send_admin_tenant_keyboard(self, chat_id: str):
        """
        Send the admin an inline keyboard listing all tenants.
        Each button switches to that tenant when clicked.
        """
        from app.database import get_registry_db
        from app.services.tenant_service import TenantService
        from app.models import Tenant as TenantModel

        reg_db = next(get_registry_db())
        try:
            tenants = (
                reg_db.query(TenantModel)
                .filter(TenantModel.chat_id != chat_id)
                .order_by(TenantModel.created_at)
                .all()
            )
        finally:
            reg_db.close()

        if not tenants:
            await self.application.bot.send_message(
                chat_id=int(chat_id),
                text="No tenants registered yet."
            )
            return

        # Build one button per tenant, callback_data = "switch:<chat_id>"
        buttons = [
            [InlineKeyboardButton(
                text=t.business_name or t.chat_id,
                callback_data=f"switch:{t.chat_id}"
            )]
            for t in tenants
        ]
        keyboard = InlineKeyboardMarkup(buttons)

        await self.application.bot.send_message(
            chat_id=int(chat_id),
            text="👥 *Select a tenant to manage:*",
            parse_mode="Markdown",
            reply_markup=keyboard,
        )

    async def handle_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """
        Handle inline keyboard button presses.

        Supported callback_data formats:
          switch:<chat_id>   — admin tenant switching
          choose:<value>     — generic choice selection (sends value as a new message)
        """
        query = update.callback_query
        await query.answer()

        chat_id = str(query.from_user.id)
        data = query.data or ""

        if data.startswith("switch:"):
            target_chat_id = data[len("switch:"):]
            response = self.handler._handle_switch_command(chat_id, f"/switch {target_chat_id}")
            try:
                await query.edit_message_text(response, parse_mode="Markdown")
            except Exception:
                await query.edit_message_text(response)

        elif data.startswith("choose:"):
            # User picked an option — treat it as if they typed it
            chosen = data[len("choose:"):]
            try:
                await query.edit_message_text(f"✅ Selected: *{chosen}*", parse_mode="Markdown")
            except Exception:
                pass
            # Inject the choice as a new message into the conversation
            if update.effective_message:
                registry_db = next(get_registry_db())
                try:
                    tenant = TenantService(registry_db).get_or_create_tenant(chat_id)
                    tenant_id = tenant.tenant_id
                finally:
                    registry_db.close()

                try:
                    response = await asyncio.wait_for(
                        self.handler.handle_text(tenant_id, chat_id, chosen),
                        timeout=REQUEST_TIMEOUT,
                    )
                    await update.effective_message.reply_text(
                        response, parse_mode="Markdown"
                    )
                except Exception as e:
                    logger.error(f"Error handling choice callback: {e}", exc_info=True)

    async def _send(self, update: Update, text: str):
        """Send with Markdown, fall back to plain text."""
        try:
            await update.message.reply_text(text, parse_mode="Markdown")
        except Exception:
            await update.message.reply_text(text)

    def _build_choice_keyboard(self, response: str):
        """
        Parse a CHOOSE: marker and return (display_text, InlineKeyboardMarkup).

        Format: CHOOSE:<title>\n<option1>\n<option2>\n...

        Returns (None, None) if no CHOOSE: marker found.
        """
        if "CHOOSE:" not in response:
            return None, None

        # Split at CHOOSE: marker
        before, rest = response.split("CHOOSE:", 1)
        lines = rest.strip().split("\n")
        title = lines[0].strip() if lines else "Please choose:"
        options = [l.strip() for l in lines[1:] if l.strip()]

        if not options:
            return None, None

        buttons = [
            [InlineKeyboardButton(text=opt, callback_data=f"choose:{opt}")]
            for opt in options
        ]
        display = (before.strip() + "\n\n" + title).strip() if before.strip() else title
        return display, InlineKeyboardMarkup(buttons)

    async def _edit(self, message, text: str):
        """Edit an existing message with Markdown, fall back to plain text."""
        try:
            await message.edit_text(text, parse_mode="Markdown")
        except Exception:
            try:
                await message.edit_text(text)
            except Exception:
                pass  # message may have been deleted or is unchanged

    async def handle_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Receive text → show thinking indicator → resolve tenant → delegate → reply."""
        try:
            if not update.message or not update.message.text:
                return

            chat_id = str(update.message.chat_id)
            text = update.message.text.strip()
            logger.info(f"Text from {chat_id}: {text}")

            # Admin: show tenant picker on /switch with no argument, or on first message
            is_admin = bool(settings.ADMIN_CHAT_ID) and chat_id == settings.ADMIN_CHAT_ID
            if is_admin and (text == "/switch" or (not self.handler._history.get(chat_id) and not self.handler._admin_target.get(chat_id))):
                await self._send_admin_tenant_keyboard(chat_id)
                if text == "/switch":
                    return  # keyboard is the full response for bare /switch

            # Show "Thinking..." immediately so the user knows we're working
            thinking_msg = await update.message.reply_text("💭 Thinking...")

            # Step 1: resolve tenant from the shared registry
            registry_db = next(get_registry_db())
            try:
                tenant = TenantService(registry_db).get_or_create_tenant(chat_id)
                tenant_id = tenant.tenant_id
            finally:
                registry_db.close()

            # Step 2: process with timeout — handler opens its own DB session
            try:
                response = await asyncio.wait_for(
                    self.handler.handle_text(tenant_id, chat_id, text),
                    timeout=REQUEST_TIMEOUT,
                )
            except asyncio.TimeoutError:
                logger.warning(f"Request timed out for chat_id={chat_id}")
                response = (
                    "⏱ That took too long to process. Please try again.\n"
                    "If this keeps happening, try breaking your request into smaller steps."
                )

            # File response (e.g. invoice PDF)
            if isinstance(response, tuple):
                pdf_bytes, filename = response
                await update.message.reply_document(
                    document=pdf_bytes,
                    filename=filename,
                    caption="📄 Here's your invoice!",
                )
            else:
                # Check for CHOOSE: inline keyboard marker
                display, keyboard = self._build_choice_keyboard(response)
                if keyboard:
                    await self._edit(thinking_msg, display)
                    await update.message.reply_text(
                        display, parse_mode="Markdown", reply_markup=keyboard
                    )
                    # Remove the thinking message since we sent a new one
                    try:
                        await thinking_msg.delete()
                    except Exception:
                        pass
                else:
                    await self._edit(thinking_msg, response)

        except Exception as e:
            logger.error(f"Error handling message: {e}", exc_info=True)
            try:
                chat_id = str(update.message.chat_id) if update.message else "unknown"
                text = update.message.text.strip() if update.message and update.message.text else ""
                error_msg = await ErrorHandler.handle(e, chat_id, context=text)
                await update.message.reply_text(error_msg)
            except Exception:
                pass

    async def handle_photo(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Receive photo → download bytes → delegate → reply."""
        try:
            if not update.message or not update.message.photo:
                return

            chat_id = str(update.message.chat_id)
            caption = (update.message.caption or "").strip()
            logger.info(f"Photo from {chat_id}, caption: '{caption}'")

            # Download highest-quality photo
            photo_bytes = bytes(
                await (await update.message.photo[-1].get_file()).download_as_bytearray()
            )

            # Step 1: resolve tenant
            registry_db = next(get_registry_db())
            try:
                tenant = TenantService(registry_db).get_or_create_tenant(chat_id)
                tenant_id = tenant.tenant_id
            finally:
                registry_db.close()

            # Step 2: process image — show a progress indicator that updates
            # every 15 seconds so the user knows it's still working
            thinking_msg = await update.message.reply_text("🔍 Processing image...")

            # Progress messages shown while waiting (cycled every 15s)
            _progress = [
                "🔍 Reading image...",
                "🧠 Extracting data...",
                "⚙️ Saving to catalog...",
                "📦 Almost done...",
            ]

            async def _update_progress():
                """Edit the thinking message every 15s to show it's still working."""
                for msg in _progress:
                    await asyncio.sleep(15)
                    try:
                        await thinking_msg.edit_text(msg)
                    except Exception:
                        pass
                # After cycling through, keep repeating the last one
                while True:
                    await asyncio.sleep(15)
                    try:
                        await thinking_msg.edit_text("⏳ Still processing, please wait...")
                    except Exception:
                        pass

            # Run progress updater alongside the actual processing
            progress_task = asyncio.create_task(_update_progress())
            try:
                response = await asyncio.wait_for(
                    self.handler.handle_image(tenant_id, chat_id, photo_bytes, caption),
                    timeout=IMAGE_TIMEOUT,
                )
            except asyncio.TimeoutError:
                logger.warning(f"Image processing timed out for chat_id={chat_id}")
                response = (
                    "⏱ Image processing took too long. Please try again.\n"
                    "Make sure the image is clear and well-lit."
                )
            finally:
                progress_task.cancel()

            await self._edit(thinking_msg, response)
        except Exception as e:
            logger.error(f"Error handling photo: {e}", exc_info=True)
            try:
                chat_id = str(update.message.chat_id) if update.message else "unknown"
                caption = update.message.caption or "" if update.message else ""
                error_msg = await ErrorHandler.handle(e, chat_id, context=f"[photo] {caption}")
                await update.message.reply_text(error_msg)
            except Exception:
                pass

    async def start(self):
        logger.info("Starting Telegram bot in polling mode...")
        self.application = Application.builder().token(self.bot_token).build()

        # Wire send function now that application exists
        self.instagram.notify_owner = self._send_to_chat
        self.admin_notifier.set_send_fn(self._send_to_chat)
        # Wire ErrorHandler so all errors automatically notify admin
        ErrorHandler.set_notifier(self.admin_notifier)

        # Start webhook server in background thread (for Instagram/WhatsApp webhooks)
        self._start_webhook_server()

        self.application.add_handler(
            MessageHandler(filters.TEXT, self.handle_message)
        )
        self.application.add_handler(
            MessageHandler(filters.PHOTO, self.handle_photo)
        )
        self.application.add_handler(
            CallbackQueryHandler(self.handle_callback)
        )
        async with self.application:
            await self.application.initialize()
            await self.application.start()
            logger.info("Bot is now listening for messages...")
            if self.backup:
                self.backup.start()
            await self.application.updater.start_polling()
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
        if self.backup:
            await self.backup.stop()
        await self.handler.close()


async def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
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
