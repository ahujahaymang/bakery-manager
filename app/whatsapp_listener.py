"""
WhatsApp Cloud API Listener.

Receives WhatsApp messages via Meta webhook and routes them through
the same RequestHandler used by Telegram. Platform-agnostic by design —
the handler knows nothing about WhatsApp.

Setup (one-time, after you have a WhatsApp number):
─────────────────────────────────────────────────
1. Get a number NOT already on WhatsApp (new SIM or VoIP)
2. Go to developers.facebook.com → your Meta App → Add Product → WhatsApp
3. Register the number under WhatsApp → Phone Numbers → Add phone number
4. Copy the permanent access token and phone number ID into .env:
       WHATSAPP_TOKEN=EAAxxxxxxxx
       WHATSAPP_PHONE_NUMBER_ID=1234567890
       WHATSAPP_VERIFY_TOKEN=kitchenos_wa_2026   (any random string you choose)
5. Set webhook URL in the Meta App dashboard:
       https://kitchenos.info/whatsapp/webhook
   Subscribe to these webhook fields: messages, message_deliveries
6. Restart the server → WhatsApp messages will flow through.

Message types handled:
  text       → handle_text()
  image      → handle_image() (downloaded via Graph API)
  audio      → not supported yet, ignored gracefully
  interactive→ button/list replies mapped to handle_text()

Tenant resolution:
  WhatsApp sender phone number (e.g. "919876543210") is used as the chat_id,
  same way Telegram chat_id works. Each unique number gets its own tenant.
"""

import asyncio
import logging
from typing import Optional

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import PlainTextResponse, JSONResponse

from app.config import settings
from app.database import get_registry_db
from app.services.tenant_service import TenantService
from app.error_handler import ErrorHandler

logger = logging.getLogger(__name__)

# Timeouts — same as Telegram
REQUEST_TIMEOUT = 60
IMAGE_TIMEOUT = 180


class WhatsAppClient:
    """
    Thin wrapper around the WhatsApp Cloud API (Graph API v18).

    All sends go through this class so the token never leaks into other code.
    """

    BASE_URL = "https://graph.facebook.com/v18.0"

    def __init__(self, token: str, phone_number_id: str):
        self._token = token
        self._phone_id = phone_number_id
        self._headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }

    async def send_text(self, to: str, text: str) -> None:
        """Send a plain text message. `to` is the E.164 number without '+'."""
        payload = {
            "messaging_product": "whatsapp",
            "to": to,
            "type": "text",
            "text": {"body": _strip_markdown(text)},
        }
        await self._post("/messages", payload)

    async def send_document(self, to: str, filename: str, data: bytes, caption: str = "") -> None:
        """Upload a document and send it. Used for PDF invoices."""
        # Step 1: upload the media
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(
                f"{self.BASE_URL}/{self._phone_id}/media",
                headers={"Authorization": f"Bearer {self._token}"},
                files={"file": (filename, data, "application/pdf")},
                data={"messaging_product": "whatsapp"},
            )
            resp.raise_for_status()
            media_id = resp.json()["id"]

        # Step 2: send the document message
        payload = {
            "messaging_product": "whatsapp",
            "to": to,
            "type": "document",
            "document": {
                "id": media_id,
                "filename": filename,
                "caption": caption,
            },
        }
        await self._post("/messages", payload)

    async def download_media(self, media_id: str) -> bytes:
        """Download media (image/audio) by media_id. Returns raw bytes."""
        async with httpx.AsyncClient(timeout=60) as client:
            # Step 1: get the download URL
            meta_resp = await client.get(
                f"{self.BASE_URL}/{media_id}",
                headers={"Authorization": f"Bearer {self._token}"},
            )
            meta_resp.raise_for_status()
            url = meta_resp.json().get("url")
            if not url:
                raise ValueError(f"No URL returned for media_id {media_id}")

            # Step 2: download the actual bytes
            media_resp = await client.get(
                url,
                headers={"Authorization": f"Bearer {self._token}"},
            )
            media_resp.raise_for_status()
            return media_resp.content

    async def mark_read(self, message_id: str) -> None:
        """Mark a message as read (shows double blue ticks)."""
        payload = {
            "messaging_product": "whatsapp",
            "status": "read",
            "message_id": message_id,
        }
        try:
            await self._post("/messages", payload)
        except Exception:
            pass  # best-effort, don't fail on mark-read errors

    async def _post(self, path: str, payload: dict) -> dict:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                f"{self.BASE_URL}/{self._phone_id}{path}",
                headers=self._headers,
                json=payload,
            )
            if not resp.is_success:
                logger.error(f"WhatsApp API error {resp.status_code}: {resp.text[:300]}")
                resp.raise_for_status()
            return resp.json()


class WhatsAppListener:
    """
    WhatsApp protocol adapter.

    Mirrors TelegramBotListener: receives messages, delegates to RequestHandler,
    sends responses back. No business logic here.
    """

    def __init__(self, handler, admin_notifier=None):
        """
        Args:
            handler: RequestHandler instance (shared with Telegram)
            admin_notifier: AdminNotifier instance for error reporting
        """
        self.handler = handler
        self.admin_notifier = admin_notifier

        token = settings.WHATSAPP_TOKEN
        phone_id = settings.WHATSAPP_PHONE_NUMBER_ID

        if token and phone_id:
            self.client = WhatsAppClient(token, phone_id)
            self.enabled = True
            logger.info("WhatsApp listener initialised")
        else:
            self.client = None
            self.enabled = False
            logger.info(
                "WhatsApp disabled — set WHATSAPP_TOKEN and WHATSAPP_PHONE_NUMBER_ID to enable"
            )

    # ── Webhook handlers ───────────────────────────────────────────────────

    def verify_webhook(self, mode: str, token: str, challenge: str) -> str:
        """Handle Meta's hub.challenge verification GET."""
        verify_token = settings.WHATSAPP_VERIFY_TOKEN
        if mode == "subscribe" and token == verify_token:
            logger.info("WhatsApp webhook verified")
            return challenge
        logger.warning(f"WhatsApp webhook verification failed: token={token!r}")
        raise HTTPException(status_code=403, detail="Verification failed")

    async def handle_webhook(self, body: dict) -> None:
        """
        Dispatch incoming webhook payload. Called in a background task so the
        webhook endpoint returns 200 immediately (Meta requires <5s response).
        """
        for entry in body.get("entry", []):
            for change in entry.get("changes", []):
                value = change.get("value", {})
                for msg in value.get("messages", []):
                    asyncio.create_task(self._handle_message(msg))

    async def _handle_message(self, msg: dict) -> None:
        """Process a single incoming message object from the webhook payload."""
        msg_type = msg.get("type")
        from_number = msg.get("from")  # e.g. "919876543210"
        msg_id = msg.get("id")

        if not from_number:
            return

        # Mark as read immediately (shows double blue ticks)
        if self.client and msg_id:
            await self.client.mark_read(msg_id)

        # Resolve tenant
        reg_db = next(get_registry_db())
        try:
            tenant = TenantService(reg_db).get_or_create_tenant(
                from_number, platform="whatsapp"
            )
            tenant_id = tenant.tenant_id
        finally:
            reg_db.close()

        # Route by message type
        try:
            if msg_type == "text":
                text = msg.get("text", {}).get("body", "").strip()
                if text:
                    await self._handle_text(tenant_id, from_number, text)

            elif msg_type == "image":
                media_id = msg.get("image", {}).get("id")
                caption = msg.get("image", {}).get("caption", "").strip()
                if media_id:
                    await self._handle_image(tenant_id, from_number, media_id, caption)

            elif msg_type == "interactive":
                # Button reply or list reply — extract the text value
                interactive = msg.get("interactive", {})
                itype = interactive.get("type")
                if itype == "button_reply":
                    text = interactive.get("button_reply", {}).get("title", "")
                elif itype == "list_reply":
                    text = interactive.get("list_reply", {}).get("title", "")
                else:
                    text = ""
                if text:
                    await self._handle_text(tenant_id, from_number, text)

            else:
                # audio, video, sticker, location, contacts — not supported yet
                logger.debug(f"Ignoring WhatsApp message type '{msg_type}' from {from_number}")

        except Exception as e:
            logger.error(f"Error processing WhatsApp message from {from_number}: {e}", exc_info=True)
            error_msg = await ErrorHandler.handle(e, chat_id=from_number, context=f"[{msg_type}]")
            await self.send(from_number, error_msg)

    # ── Message processing ─────────────────────────────────────────────────

    async def _handle_text(self, tenant_id, from_number: str, text: str) -> None:
        """Run handler.handle_text and send the response."""
        try:
            response = await asyncio.wait_for(
                self.handler.handle_text(tenant_id, from_number, text),
                timeout=REQUEST_TIMEOUT,
            )
        except asyncio.TimeoutError:
            response = (
                "⏱ That took too long to process. Please try again, or break your "
                "request into smaller steps."
            )

        await self._send_response(from_number, response)

    async def _handle_image(
        self, tenant_id, from_number: str, media_id: str, caption: str
    ) -> None:
        """Download the image and run handler.handle_image."""
        if not self.client:
            return

        await self.send(from_number, "🔍 Processing image, please wait…")

        try:
            image_bytes = await self.client.download_media(media_id)
        except Exception as e:
            logger.error(f"Failed to download WhatsApp media {media_id}: {e}")
            await self.send(from_number, "❌ Could not download the image. Please try again.")
            return

        try:
            response = await asyncio.wait_for(
                self.handler.handle_image(tenant_id, from_number, image_bytes, caption),
                timeout=IMAGE_TIMEOUT,
            )
        except asyncio.TimeoutError:
            response = (
                "⏱ Image processing took too long. Please try again with a clearer image."
            )

        await self._send_response(from_number, response)

    async def _send_response(self, to: str, response) -> None:
        """Send a response — handles text, PDF tuples, and CHOOSE: markers."""
        if isinstance(response, tuple):
            # PDF invoice — (bytes, filename)
            pdf_bytes, filename = response
            await self.send_document(to, filename, pdf_bytes, caption="📄 Here's your invoice!")
        elif isinstance(response, str) and "CHOOSE:" in response:
            # Convert CHOOSE: to an interactive button list (max 3 buttons)
            # or fall back to plain text with numbered options
            await self._send_choose(to, response)
        else:
            await self.send(to, response or "Done.")

    async def _send_choose(self, to: str, response: str) -> None:
        """
        Send a CHOOSE: response as a WhatsApp interactive message if possible,
        otherwise as numbered plain text.

        WhatsApp interactive buttons: max 3 options, each title max 20 chars.
        For more options, send as a list message (max 10 rows).
        """
        before, rest = response.split("CHOOSE:", 1)
        lines = rest.strip().split("\n")
        title = lines[0].strip() if lines else "Please choose:"
        options = [l.strip() for l in lines[1:] if l.strip()]

        if not options:
            await self.send(to, response)
            return

        prefix = (before.strip() + "\n\n") if before.strip() else ""
        body_text = prefix + title

        # Try interactive buttons (≤3 short options)
        if len(options) <= 3 and all(len(o) <= 20 for o in options):
            payload = {
                "messaging_product": "whatsapp",
                "to": to,
                "type": "interactive",
                "interactive": {
                    "type": "button",
                    "body": {"text": body_text[:1024]},
                    "action": {
                        "buttons": [
                            {"type": "reply", "reply": {"id": str(i), "title": opt[:20]}}
                            for i, opt in enumerate(options)
                        ]
                    },
                },
            }
            try:
                await self.client._post("/messages", payload)
                return
            except Exception as e:
                logger.warning(f"Interactive buttons failed, falling back to text: {e}")

        # Try list message (≤10 options)
        if len(options) <= 10:
            payload = {
                "messaging_product": "whatsapp",
                "to": to,
                "type": "interactive",
                "interactive": {
                    "type": "list",
                    "body": {"text": body_text[:1024]},
                    "action": {
                        "button": "Choose",
                        "sections": [{
                            "title": "Options",
                            "rows": [
                                {"id": str(i), "title": opt[:24], "description": ""}
                                for i, opt in enumerate(options)
                            ],
                        }],
                    },
                },
            }
            try:
                await self.client._post("/messages", payload)
                return
            except Exception as e:
                logger.warning(f"Interactive list failed, falling back to text: {e}")

        # Plain text fallback — number the options
        numbered = "\n".join(f"{i+1}. {opt}" for i, opt in enumerate(options))
        await self.send(to, f"{body_text}\n\n{numbered}\n\nReply with the number of your choice.")

    # ── Public send helpers ────────────────────────────────────────────────

    async def send(self, to: str, text: str) -> None:
        """Send a text message. No-op if WhatsApp is not configured."""
        if not self.client or not self.enabled:
            logger.debug(f"WhatsApp send skipped (not configured): to={to} text={text[:50]}")
            return
        try:
            await self.client.send_text(to, text)
        except Exception as e:
            logger.error(f"Failed to send WhatsApp message to {to}: {e}")

    async def send_document(self, to: str, filename: str, data: bytes, caption: str = "") -> None:
        """Send a document. No-op if WhatsApp is not configured."""
        if not self.client or not self.enabled:
            return
        try:
            await self.client.send_document(to, filename, data, caption)
        except Exception as e:
            logger.error(f"Failed to send WhatsApp document to {to}: {e}")


# ── FastAPI router ─────────────────────────────────────────────────────────────

def create_whatsapp_router(listener: "WhatsAppListener"):
    """Create FastAPI routes for WhatsApp webhook. Registered at startup."""
    router = APIRouter(prefix="/whatsapp", tags=["whatsapp"])

    @router.get("/webhook")
    async def verify(request: Request):
        """Meta hub.challenge verification (GET)."""
        params = request.query_params
        challenge = listener.verify_webhook(
            params.get("hub.mode", ""),
            params.get("hub.verify_token", ""),
            params.get("hub.challenge", ""),
        )
        return PlainTextResponse(challenge)

    @router.post("/webhook")
    async def webhook(request: Request):
        """Receive WhatsApp messages and status updates."""
        # Return 200 immediately — Meta will retry on non-200
        body = await request.json()
        # Only process 'messages' changes, ignore delivery receipts etc.
        for entry in body.get("entry", []):
            for change in entry.get("changes", []):
                if change.get("field") == "messages":
                    asyncio.create_task(listener.handle_webhook(body))
                    break
        return JSONResponse({"status": "ok"})

    @router.get("/status")
    async def status():
        """Check if WhatsApp is configured and active."""
        return {
            "enabled": listener.enabled,
            "phone_number_id": settings.WHATSAPP_PHONE_NUMBER_ID or None,
            "webhook_url": f"{settings.WEBHOOK_URL}/whatsapp/webhook" if settings.WEBHOOK_URL else None,
        }

    return router


# ── Helpers ────────────────────────────────────────────────────────────────────

def _strip_markdown(text: str) -> str:
    """
    Convert Telegram-style Markdown to plain text for WhatsApp.

    WhatsApp uses its own formatting (*bold*, _italic_) which is close
    but not identical to Telegram Markdown. We keep * and _ as-is since
    WhatsApp renders them natively. We just strip backtick code formatting
    and convert [text](url) links to "text: url" since WhatsApp doesn't
    support inline links.
    """
    import re
    # Convert [text](url) → text (url)
    text = re.sub(r'\[([^\]]+)\]\(([^)]+)\)', r'\1: \2', text)
    # Remove triple backticks (code blocks)
    text = re.sub(r'```[a-z]*\n?', '', text)
    return text
