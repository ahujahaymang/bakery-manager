"""
Instagram DM Listener.

Receives Instagram DM webhooks from Meta, buffers conversations per thread,
and detects confirmed orders after a period of inactivity.

When an order is detected, it notifies the bakery owner on their primary
platform (Telegram/WhatsApp) for confirmation before creating the order.

Setup:
1. Create a Meta Developer app with Instagram Messaging permission
2. Set webhook URL to: https://yourdomain.com/instagram/webhook
3. Set INSTAGRAM_VERIFY_TOKEN in .env
4. Connect each bakery owner's Instagram account via OAuth
   (see /instagram/connect endpoint)
"""

import asyncio
import logging
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Dict, List, Optional
from uuid import UUID

from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import PlainTextResponse, JSONResponse

from app.config import settings
from app.database import get_registry_db, get_db
from app.models import Tenant
from app.services.instagram_order_detector import InstagramOrderDetector, DetectedOrder
from app.services.llm_service import LLMService
from app.services.customer_service import CustomerService
from app.services.order_service import OrderService, OrderCreate, OrderItemCreate
from decimal import Decimal
from datetime import date

logger = logging.getLogger(__name__)

# How long to wait after last message before analysing the conversation
CONVERSATION_TIMEOUT_MINUTES = 30


class InstagramListener:
    """
    Handles Instagram DM webhooks and detects orders.

    Conversation buffering:
    - Messages arrive one at a time via webhook
    - We buffer them per thread_id
    - After CONVERSATION_TIMEOUT_MINUTES of silence, we analyse the thread
    - If an order is detected, we notify the owner for confirmation

    Owner notification:
    - Sends a message to the owner's Telegram/WhatsApp chat
    - Owner replies "yes" or "no" to confirm or discard
    - On "yes" → order is created in the database
    """

    def __init__(self, notify_owner_fn=None):
        """
        Args:
            notify_owner_fn: async callable(chat_id, message) to notify the owner.
                             Injected at startup to avoid circular imports.
        """
        self.detector = InstagramOrderDetector(LLMService())
        self.notify_owner = notify_owner_fn

        # {thread_id: [message_dicts]}
        self._buffers: Dict[str, List[dict]] = defaultdict(list)
        # {thread_id: asyncio.TimerHandle}
        self._timers: Dict[str, asyncio.Task] = {}
        # Pending confirmations: {owner_chat_id: DetectedOrder}
        self._pending: Dict[str, DetectedOrder] = {}

    # ── Webhook verification ───────────────────────────────────────────────

    def verify_webhook(self, mode: str, token: str, challenge: str) -> str:
        """Handle Meta's webhook verification GET request."""
        if mode == "subscribe" and token == settings.INSTAGRAM_VERIFY_TOKEN:
            logger.info("Instagram webhook verified")
            return challenge
        raise HTTPException(status_code=403, detail="Verification failed")

    # ── Message handling ───────────────────────────────────────────────────

    async def handle_webhook(self, body: dict):
        """Process incoming Instagram webhook payload."""
        for entry in body.get("entry", []):
            ig_account_id = entry.get("id")
            for messaging in entry.get("messaging", []):
                await self._handle_message(ig_account_id, messaging)

    async def _handle_message(self, ig_account_id: str, messaging: dict):
        """Buffer a single DM message and reset the inactivity timer."""
        sender_id = messaging.get("sender", {}).get("id")
        recipient_id = messaging.get("recipient", {}).get("id")
        message = messaging.get("message", {})
        text = message.get("text", "")

        if not text or not sender_id:
            return

        # The bakery owner's account is the recipient
        # The customer is the sender
        # thread_id = combination of both IDs
        thread_id = f"{ig_account_id}_{sender_id}"

        # Determine sender label
        is_owner = sender_id == ig_account_id
        sender_label = "Owner" if is_owner else f"@{sender_id}"

        self._buffers[thread_id].append({
            "sender": sender_label,
            "text": text,
            "timestamp": datetime.utcnow().isoformat(),
        })

        logger.info(f"Instagram DM buffered: thread={thread_id}, sender={sender_label}")

        # Reset inactivity timer
        if thread_id in self._timers:
            self._timers[thread_id].cancel()

        self._timers[thread_id] = asyncio.create_task(
            self._analyse_after_timeout(thread_id, ig_account_id, sender_id)
        )

    async def _analyse_after_timeout(
        self, thread_id: str, ig_account_id: str, customer_ig_id: str
    ):
        """Wait for inactivity, then analyse the conversation for orders."""
        await asyncio.sleep(CONVERSATION_TIMEOUT_MINUTES * 60)

        messages = self._buffers.pop(thread_id, [])
        self._timers.pop(thread_id, None)

        if not messages:
            return

        logger.info(f"Analysing conversation thread {thread_id} ({len(messages)} messages)")

        # Find which tenant owns this Instagram account
        tenant = self._find_tenant_by_instagram(ig_account_id)
        if not tenant:
            logger.warning(f"No tenant found for Instagram account {ig_account_id}")
            return

        # Detect order
        detected = await self.detector.detect_order(messages, customer_ig_id)
        if not detected:
            logger.info(f"No order detected in thread {thread_id}")
            return

        logger.info(f"Order detected in thread {thread_id} (confidence: {detected.confidence:.0%})")

        # Notify owner for confirmation
        await self._notify_owner_for_confirmation(tenant, detected)

    def _find_tenant_by_instagram(self, ig_account_id: str) -> Optional[Tenant]:
        """Look up which tenant has this Instagram account connected."""
        reg_db = next(get_registry_db())
        try:
            return reg_db.query(Tenant).filter(
                Tenant.instagram_account_id == ig_account_id
            ).first()
        finally:
            reg_db.close()

    async def _notify_owner_for_confirmation(self, tenant: Tenant, order: DetectedOrder):
        """Send the detected order to the owner for confirmation via their primary platform."""
        if not self.notify_owner:
            logger.warning("No notify_owner function configured")
            return

        # Route to the owner's registered messaging platform
        platform = getattr(tenant, 'messaging_platform', 'telegram')
        logger.info(f"Notifying owner {tenant.chat_id} via {platform}")

        # Store pending confirmation
        self._pending[tenant.chat_id] = order

        # Format notification
        items_text = "\n".join(
            f"  • {item['name']} x{item.get('quantity', 1)}"
            + (f" @ ₹{item['price']}" if item.get('price') else "")
            for item in order.items
        )

        msg = (
            f"📱 *New order from Instagram* (@{order.customer_instagram_handle})\n\n"
        )
        if order.customer_name:
            msg += f"*Customer:* {order.customer_name}\n"
        if order.delivery_date:
            msg += f"*Delivery:* {order.delivery_date}\n"
        if order.delivery_address:
            msg += f"*Address:* {order.delivery_address}\n"
        msg += f"\n*Items:*\n{items_text}\n\n"
        msg += f"_(Confidence: {order.confidence:.0%})_\n\n"
        msg += "Reply *yes* to create this order, or *no* to discard."

        await self.notify_owner(tenant.chat_id, msg)

    # ── Confirmation handling ──────────────────────────────────────────────

    def has_pending_confirmation(self, chat_id: str) -> bool:
        """Check if this owner has a pending Instagram order confirmation."""
        return chat_id in self._pending

    async def handle_confirmation(
        self, chat_id: str, tenant_id: UUID, response: str
    ) -> str:
        """
        Handle owner's yes/no response to an Instagram order notification.

        Returns a message to send back to the owner.
        """
        order = self._pending.pop(chat_id, None)
        if not order:
            return None  # no pending confirmation

        if response.strip().lower() not in ("yes", "y", "confirm", "ok", "हाँ"):
            return "❌ Order discarded."

        # Create the order
        try:
            db = next(get_db(tenant_id))
            try:
                result = await self._create_order_from_detection(db, tenant_id, order)
                return result
            finally:
                db.close()
        except Exception as e:
            logger.error(f"Failed to create Instagram order: {e}", exc_info=True)
            return f"❌ Failed to create order: {str(e)}"

    async def _create_order_from_detection(
        self, db, tenant_id: UUID, order: DetectedOrder
    ) -> str:
        """Create an order from detected Instagram DM data."""
        cust_svc = CustomerService(db)
        order_svc = OrderService(db)

        # Find or create customer by Instagram handle
        customers = cust_svc.get_customer(tenant_id, order.customer_instagram_handle)
        if not customers and order.customer_name:
            customers = cust_svc.get_customer(tenant_id, order.customer_name)

        if not customers:
            # Create new customer from Instagram
            name = order.customer_name or f"@{order.customer_instagram_handle}"
            customer = cust_svc.create_customer(
                tenant_id, name,
                phone=f"ig:{order.customer_instagram_handle}",
                address=order.delivery_address
            )
        else:
            customer = customers[0]

        # Build order items
        items = []
        for item in order.items:
            price = Decimal(str(item.get("price", 0))) if item.get("price") else Decimal("0")
            items.append(OrderItemCreate(
                recipe_name=item["name"],
                quantity=int(item.get("quantity", 1)),
                selling_price=price
            ))

        if not items:
            return "❌ Could not extract order items. Please create the order manually."

        delivery_date = (
            date.fromisoformat(order.delivery_date)
            if order.delivery_date
            else date.today()
        )

        new_order = order_svc.create_order(
            tenant_id,
            OrderCreate(
                customer_identifier=customer.phone,
                delivery_date=delivery_date,
                items=items,
                delivery_address=order.delivery_address or customer.address
            )
        )

        lines = [
            f"✅ Order created from Instagram DM!",
            f"Customer: {customer.name}",
            f"Delivery: {new_order.delivery_date}",
        ]
        if new_order.delivery_address:
            lines.append(f"Address: {new_order.delivery_address}")
        lines.append("Items:")
        for item in items:
            lines.append(f"  • {item.recipe_name} x{item.quantity}")

        return "\n".join(lines)


# ── FastAPI routes ─────────────────────────────────────────────────────────

def create_instagram_router(listener: InstagramListener):
    """Create FastAPI router for Instagram webhook endpoints."""
    from fastapi import APIRouter
    router = APIRouter(prefix="/instagram")

    @router.get("/webhook")
    async def verify(request: Request):
        """Meta webhook verification."""
        params = request.query_params
        challenge = listener.verify_webhook(
            params.get("hub.mode", ""),
            params.get("hub.verify_token", ""),
            params.get("hub.challenge", ""),
        )
        return PlainTextResponse(challenge)

    @router.post("/webhook")
    async def webhook(request: Request):
        """Receive Instagram DM events."""
        body = await request.json()
        asyncio.create_task(listener.handle_webhook(body))
        return JSONResponse({"status": "ok"})

    @router.get("/connect")
    async def connect(request: Request):
        """
        Start Instagram OAuth flow for a tenant.
        URL: /instagram/connect?tenant_id=<uuid>
        """
        tenant_id = request.query_params.get("tenant_id")
        if not tenant_id:
            raise HTTPException(400, "tenant_id required")

        # Build Meta OAuth URL
        oauth_url = (
            f"https://www.facebook.com/v18.0/dialog/oauth"
            f"?client_id={settings.META_APP_ID}"
            f"&redirect_uri={settings.META_REDIRECT_URI}"
            f"&scope=instagram_basic,instagram_manage_messages,pages_messaging"
            f"&state={tenant_id}"
        )
        from fastapi.responses import RedirectResponse
        return RedirectResponse(oauth_url)

    @router.get("/callback")
    async def oauth_callback(request: Request):
        """
        Handle Meta OAuth callback after owner grants Instagram permission.
        Stores the access token and Instagram account ID for the tenant.
        """
        code = request.query_params.get("code")
        tenant_id = request.query_params.get("state")

        if not code or not tenant_id:
            raise HTTPException(400, "Missing code or state")

        import httpx

        # Exchange code for access token
        async with httpx.AsyncClient() as client:
            token_resp = await client.get(
                "https://graph.facebook.com/v18.0/oauth/access_token",
                params={
                    "client_id": settings.META_APP_ID,
                    "client_secret": settings.META_APP_SECRET,
                    "redirect_uri": settings.META_REDIRECT_URI,
                    "code": code,
                }
            )
            token_data = token_resp.json()
            access_token = token_data.get("access_token")

            if not access_token:
                raise HTTPException(400, f"Failed to get access token: {token_data}")

            # Get Instagram account ID
            me_resp = await client.get(
                "https://graph.facebook.com/v18.0/me",
                params={"access_token": access_token, "fields": "id,name"}
            )
            me_data = me_resp.json()
            ig_account_id = me_data.get("id")

        # Store in tenant registry
        reg_db = next(get_registry_db())
        try:
            tenant = reg_db.query(Tenant).filter(
                Tenant.tenant_id == tenant_id
            ).first()
            if not tenant:
                raise HTTPException(404, "Tenant not found")
            tenant.instagram_account_id = ig_account_id
            tenant.instagram_access_token = access_token
            reg_db.commit()
            logger.info(f"Instagram connected for tenant {tenant_id}: account {ig_account_id}")
        finally:
            reg_db.close()

        return JSONResponse({
            "status": "connected",
            "instagram_account_id": ig_account_id,
            "message": "Instagram connected successfully! You can now receive orders via DM."
        })

    return router
