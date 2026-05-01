"""
Platform-agnostic request handler.

Owns the agent loop, conversation history, and image processing logic.
Works with any messaging platform — Telegram, WhatsApp, Slack, etc.
The caller supplies: chat_id, db session, and raw input (text or image).
"""

import logging
from collections import defaultdict
from typing import Dict, List, Optional
from uuid import UUID

from app.services.agent_service import AgentService
from app.services.tool_executor import ToolExecutor
from app.services.image_service import ImageService
from app.services.llm_service import LLMService
from app.config import settings

logger = logging.getLogger(__name__)

# Number of message turns kept in memory per conversation
MAX_HISTORY = 20


class RequestHandler:
    """
    Platform-agnostic handler that runs the LLM agent for every request.

    Responsibilities:
    - Maintain per-chat conversation history (in-memory, last MAX_HISTORY turns)
    - Route text messages through the LLM agent
    - Process images: extract structured data, summarise, pass to agent
    - Handle admin impersonation for multi-tenant management
    - Return a plain string response for the caller to deliver

    The caller (telegram_listener, whatsapp_listener, etc.) only handles
    platform I/O: receiving messages, downloading files, sending replies.
    """

    def __init__(self):
        self.agent = AgentService()
        self.llm_service = LLMService()
        self.image_service = ImageService(self.llm_service)
        # Per-chat message history: {chat_id: [{role, content}, ...]}
        self._history: Dict[str, List[dict]] = defaultdict(list)
        # Admin tenant override: {admin_chat_id: tenant_id}
        self._admin_target: Dict[str, UUID] = {}

    # ── History ────────────────────────────────────────────────────────────

    def _get_history(self, chat_id: str) -> List[dict]:
        """Return the conversation history for a chat."""
        return self._history[chat_id]

    def _append(self, chat_id: str, role: str, content: str) -> None:
        """Append a message to history, trimming to MAX_HISTORY."""
        history = self._history[chat_id]
        history.append({"role": role, "content": content})
        if len(history) > MAX_HISTORY:
            self._history[chat_id] = history[-MAX_HISTORY:]

    # ── Public API ─────────────────────────────────────────────────────────

    async def handle_text(self, db, tenant_id: UUID, chat_id: str, text: str) -> str:
        """
        Process a text message and return the agent's response.

        Handles three cases in order:
        1. Admin /switch command — list or switch active tenant
        2. Admin message — route to configured tenant, prefix response with label
        3. New user (no history) — return welcome message
        4. Regular message — run agent with conversation history

        Args:
            db: Database session
            tenant_id: Tenant UUID resolved from chat_id (may be overridden for admin)
            chat_id: Unique conversation identifier
            text: User's message text

        Returns:
            Response string to deliver to the user
        """
        if self._is_admin(chat_id):
            return await self._handle_admin_message(db, tenant_id, chat_id, text)

        if not self._get_history(chat_id):
            return self._first_contact_message()

        return await self._run_agent(db, tenant_id, chat_id, text)

    async def handle_image(
        self,
        db,
        tenant_id: UUID,
        chat_id: str,
        image_bytes: bytes,
        caption: str,
    ) -> Optional[str]:
        """
        Process an image message and return the agent's response.

        Determines image type from caption keywords, extracts structured data
        via GPT-4o Vision, summarises it as a natural language instruction,
        then passes it to the agent.

        Args:
            db: Database session
            tenant_id: Tenant UUID
            chat_id: Unique conversation identifier
            image_bytes: Raw image bytes
            caption: User-provided caption (determines image type)

        Returns:
            Response string, or None if caption is missing/unrecognised
            (caller should prompt the user to add a caption)
        """
        image_type = self._detect_image_type(caption)
        if image_type is None:
            return None

        result = await self._extract_image_data(image_type, image_bytes)
        if "error" in result:
            return f"⚠️ {result['error']}"

        summary = self._summarise_image_result(image_type, result)
        logger.info(f"Image summary ({image_type}): {summary[:200]}")

        return await self._run_agent(
            db, tenant_id, chat_id,
            user_message=summary,
            history_label=f"[Image: {image_type}]",
        )

    async def close(self) -> None:
        """Shut down the agent and LLM client."""
        await self.agent.close()

    # ── Admin ──────────────────────────────────────────────────────────────

    def _is_admin(self, chat_id: str) -> bool:
        """Return True if this chat_id belongs to the configured admin."""
        return bool(settings.ADMIN_CHAT_ID) and chat_id == settings.ADMIN_CHAT_ID

    async def _handle_admin_message(
        self, db, tenant_id: UUID, chat_id: str, text: str
    ) -> str:
        """Route admin messages: /switch command or regular agent call."""
        if text.startswith("/switch"):
            return self._handle_switch_command(chat_id, text)

        active_tenant_id = self._resolve_admin_tenant(chat_id, tenant_id)
        label = self._admin_label(db, active_tenant_id)
        response = await self._run_agent(db, active_tenant_id, chat_id, text)
        return f"_{label}_\n\n{response}"

    def _resolve_admin_tenant(self, admin_chat_id: str, fallback_tenant_id: UUID) -> UUID:
        """
        Determine which tenant the admin should operate as.

        Resolution order:
        1. Already switched this session → use cached target
        2. OWNER_CHAT_ID configured (dedicated deployment) → use that tenant
        3. Scan DB for first non-admin tenant (shared deployment)
        4. Fall back to admin's own tenant
        """
        if admin_chat_id in self._admin_target:
            return self._admin_target[admin_chat_id]

        if settings.OWNER_CHAT_ID:
            from app.services.tenant_service import TenantService
            from app.database import get_registry_db
            reg_db = next(get_registry_db())
            try:
                tenant = TenantService(reg_db).get_or_create_tenant(settings.OWNER_CHAT_ID)
                self._admin_target[admin_chat_id] = tenant.tenant_id
                return tenant.tenant_id
            finally:
                reg_db.close()

        from app.models import Tenant
        from app.database import get_registry_db
        reg_db = next(get_registry_db())
        try:
            first = (
                reg_db.query(Tenant)
                .filter(Tenant.chat_id != admin_chat_id)
                .order_by(Tenant.created_at)
                .first()
            )
            if first:
                self._admin_target[admin_chat_id] = first.tenant_id
                return first.tenant_id
        finally:
            reg_db.close()

        return fallback_tenant_id

    def _admin_label(self, db, tenant_id: UUID) -> str:
        """Short label shown above every admin response."""
        if settings.OWNER_CHAT_ID:
            return "Admin view"

        from app.models import Tenant
        from app.database import get_registry_db
        reg_db = next(get_registry_db())
        try:
            tenant = reg_db.query(Tenant).filter(Tenant.tenant_id == tenant_id).first()
            return f"Admin view — tenant {tenant.chat_id}" if tenant else "Admin view"
        finally:
            reg_db.close()

    def _handle_switch_command(self, db, admin_chat_id: str, text: str) -> str:
        parts = text.strip().split(maxsplit=1)
        if len(parts) < 2:
            return self._list_tenants(admin_chat_id)

        target_chat_id = parts[1].strip()
        from app.models import Tenant
        from app.database import get_registry_db
        reg_db = next(get_registry_db())
        try:
            tenant = reg_db.query(Tenant).filter(Tenant.chat_id == target_chat_id).first()
            if not tenant:
                return f"❌ No tenant found with chat_id `{target_chat_id}`"
            self._admin_target[admin_chat_id] = tenant.tenant_id
            self._history[admin_chat_id] = []
            return f"✅ Switched to tenant `{target_chat_id}`"
        finally:
            reg_db.close()

    def _list_tenants(self, admin_chat_id: str) -> str:
        """Return a formatted list of all non-admin tenants."""
        from app.models import Tenant
        from app.database import get_registry_db
        reg_db = next(get_registry_db())
        try:
            tenants = (
                reg_db.query(Tenant)
                .filter(Tenant.chat_id != admin_chat_id)
                .order_by(Tenant.created_at)
                .all()
            )
            if not tenants:
                return "No tenants found yet."

            current = self._admin_target.get(admin_chat_id)
            lines = ["*Available tenants:*\n"]
            for t in tenants:
                marker = " ← current" if current == t.tenant_id else ""
                lines.append(f"• `{t.chat_id}`{marker}")
            lines.append("\nUse `/switch <chat_id>` to switch.")
            return "\n".join(lines)
        finally:
            reg_db.close()

    # ── Agent ──────────────────────────────────────────────────────────────

    async def _run_agent(
        self,
        db,
        tenant_id: UUID,
        chat_id: str,
        user_message: str,
        history_label: str = "",
    ) -> str:
        """
        Run the LLM agent with the current conversation history.

        Args:
            db: Database session
            tenant_id: Tenant to operate on
            chat_id: Conversation identifier (for history)
            user_message: Message to send to the agent
            history_label: Optional prefix for the history entry (e.g. "[Image: recipe]")

        Returns:
            Agent's response string
        """
        executor = ToolExecutor(db, tenant_id)
        response = await self.agent.run(
            user_message=user_message,
            history=self._get_history(chat_id),
            tool_executor=executor.execute,
        )
        history_entry = f"{history_label} {user_message}".strip() if history_label else user_message
        self._append(chat_id, "user", history_entry)
        self._append(chat_id, "assistant", response)
        return response

    # ── Welcome ────────────────────────────────────────────────────────────

    def _first_contact_message(self) -> str:
        """
        Welcome message shown to every new user on first contact.
        Explains all capabilities so they know what to ask for.
        Seeded into history so the next message goes straight to the agent.
        """
        msg = (
            "👋 *Welcome to Operations Bot!*\n\n"
            "I'm your business assistant — just tell me what you need in plain language. "
            "No commands to memorise.\n\n"

            "📦 *Inventory*\n"
            "• Add or update ingredients and packaging\n"
            "• Check stock levels\n"
            "_'Add 5kg flour at ₹40/kg'_\n"
            "_'How much sugar do I have?'_\n\n"

            "📖 *Recipes*\n"
            "• Create recipes with ingredients and packaging\n"
            "• Calculate cost per unit\n"
            "• Edit or delete recipes\n"
            "_'Create recipe Brownies yield 12'_\n"
            "_'Show recipe Brownies'_\n"
            "_'Add 100g butter to Brownies'_\n\n"

            "👥 *Customers*\n"
            "• Add and search customers\n"
            "_'Add customer Priya, phone 9876543210'_\n\n"

            "🛒 *Orders*\n"
            "• Create, cancel, or delete orders\n"
            "• Mark orders as delivered\n"
            "• View upcoming or unpaid orders\n"
            "_'Order for Priya — 2 Brownies at ₹150 each, deliver May 10'_\n"
            "_'Show unpaid orders'_\n\n"

            "💰 *Payments*\n"
            "• Record payments against orders\n"
            "• View payment history\n"
            "_'Record ₹300 cash payment for Priya'_\n\n"

            "📊 *Reports*\n"
            "• Weekly profit breakdown\n"
            "_'Show this week\\'s profit'_\n\n"

            "📸 *Images*\n"
            "• Send a photo of a handwritten recipe with caption *recipe*\n"
            "• Send a payment receipt with caption *receipt*\n"
            "• Send an order screenshot with caption *order*\n\n"

            "What would you like to start with?"
        )
        return msg

    # ── Image processing ───────────────────────────────────────────────────

    # Keywords that identify each image type from the caption
    _IMAGE_TYPE_KEYWORDS: Dict[str, List[str]] = {
        "receipt": ["receipt", "payment", "paid", "bill"],
        "recipe":  ["recipe", "ingredients", "formula"],
        "order":   ["order", "whatsapp", "message", "sms"],
    }

    def _detect_image_type(self, caption: str) -> Optional[str]:
        """
        Determine image type from caption keywords.

        Returns the image type string, or None if unrecognised.
        """
        caption_lower = caption.lower()
        for image_type, keywords in self._IMAGE_TYPE_KEYWORDS.items():
            if any(kw in caption_lower for kw in keywords):
                return image_type
        return None

    async def _extract_image_data(self, image_type: str, image_bytes: bytes) -> dict:
        """Dispatch to the correct ImageService method based on image type."""
        extractors = {
            "receipt": self.image_service.process_receipt_image,
            "recipe":  self.image_service.process_recipe_image,
            "order":   self.image_service.process_order_image,
        }
        return await extractors[image_type](image_bytes)

    def _summarise_image_result(self, image_type: str, result: dict) -> str:
        """
        Convert extracted image data into a natural language instruction
        that the agent can act on directly.
        """
        summarisers = {
            "receipt": self._summarise_receipt,
            "recipe":  self._summarise_recipe,
            "order":   self._summarise_order,
        }
        return summarisers[image_type](result)

    def _summarise_recipe(self, result: dict) -> str:
        """Build agent instruction from extracted recipe data."""
        lines = [
            "I scanned a recipe image. Please create this recipe:",
            f"Name: {result.get('name', 'Unknown')}",
            f"Yield per batch: {result.get('yield_per_batch') or 1}",
        ]
        for ing in result.get("ingredients", []):
            lines.append(f"  Ingredient — {ing.get('item_name')}: {ing.get('quantity')} {ing.get('unit', 'pcs')}")
        for pkg in result.get("packaging", []):
            lines.append(f"  Packaging — {pkg.get('item_name')}: {pkg.get('quantity')} {pkg.get('unit', 'pcs')}")
        lines.append(
            "Create the recipe and add all components. "
            "For any missing inventory items, add them with cost 0 so the user can update later."
        )
        return "\n".join(lines)

    def _summarise_receipt(self, result: dict) -> str:
        """Build agent instruction from extracted receipt data."""
        lines = ["I scanned a payment receipt."]
        if result.get("amount"):
            lines.append(f"Amount: ₹{result['amount']}")
        if result.get("method"):
            lines.append(f"Method: {result['method']}")
        if result.get("customer_name"):
            lines.append(f"Customer: {result['customer_name']}")
        if result.get("date"):
            lines.append(f"Date: {result['date']}")
        lines.append("Please help me record this payment. Ask for any missing details.")
        return "\n".join(lines)

    def _summarise_order(self, result: dict) -> str:
        """Build agent instruction from extracted order data."""
        lines = ["I scanned an order image. Please create this order:"]
        if result.get("customer_name"):
            lines.append(f"Customer: {result['customer_name']}")
        if result.get("customer_phone"):
            lines.append(f"Phone: {result['customer_phone']}")
        if result.get("delivery_date"):
            lines.append(f"Delivery date: {result['delivery_date']}")
        for item in result.get("items", []):
            lines.append(
                f"  - {item.get('recipe_name')}: "
                f"qty {item.get('quantity')}, "
                f"price ₹{item.get('selling_price', 0)}"
            )
        lines.append("Create the order. Ask for any missing details.")
        return "\n".join(lines)
