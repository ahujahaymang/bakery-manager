"""
Platform-agnostic request handler.

Owns the agent loop, conversation history, and image processing logic.
Can be used with Telegram, WhatsApp, or any other messaging platform.
The caller only needs to supply: a chat_id, a db session, and raw input
(text or image bytes + caption).
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

MAX_HISTORY = 20


class RequestHandler:
    """
    Platform-agnostic handler that runs the LLM agent for every request.

    Responsibilities:
    - Maintain per-chat conversation history
    - Process text messages through the agent
    - Process images: extract data, summarise, pass to agent
    - Return a plain string response for the caller to send

    The caller (telegram_listener, whatsapp_listener, etc.) only handles
    platform I/O: receiving messages, downloading files, sending replies.
    """

    def __init__(self):
        self.agent = AgentService()
        self.llm_service = LLMService()
        self.image_service = ImageService(self.llm_service)
        # {chat_id: [{role, content}, ...]}
        self._history: Dict[str, List[dict]] = defaultdict(list)
        # admin impersonation: {admin_chat_id: tenant_id}
        self._admin_target: Dict[str, UUID] = {}

    # ── History management ─────────────────────────────────────────────────

    def _get_history(self, chat_id: str) -> List[dict]:
        return self._history[chat_id]

    def _append(self, chat_id: str, role: str, content: str):
        history = self._history[chat_id]
        history.append({"role": role, "content": content})
        if len(history) > MAX_HISTORY:
            self._history[chat_id] = history[-MAX_HISTORY:]

    # ── Public API ─────────────────────────────────────────────────────────

    async def handle_text(self, db, tenant_id: UUID, chat_id: str, text: str) -> str:
        """
        Process a text message and return the agent's response.

        Admin behaviour (when ADMIN_CHAT_ID matches chat_id):
        - Skips the welcome message
        - Uses the bakery's owner tenant by default (first non-admin tenant)
        - '/switch <chat_id>' lets admin impersonate a specific tenant

        New user behaviour:
        - Returns welcome message on first contact

        Args:
            db: Database session
            tenant_id: Tenant UUID resolved from chat_id (may be overridden for admin)
            chat_id: Unique identifier for this conversation
            text: User's message

        Returns:
            str: Response to send back to the user
        """
        is_admin = settings.ADMIN_CHAT_ID and chat_id == settings.ADMIN_CHAT_ID

        # Admin: handle /switch command
        if is_admin and text.startswith("/switch"):
            return self._handle_admin_switch(db, chat_id, text)

        # Admin: resolve which tenant to operate as
        if is_admin:
            tenant_id = self._resolve_admin_tenant(db, chat_id, tenant_id)
            active_label = self._admin_active_label(db, tenant_id)
            # Prefix agent responses with which bakery is active (only for admin)
            executor = ToolExecutor(db, tenant_id)
            response = await self.agent.run(
                user_message=text,
                history=self._get_history(chat_id),
                tool_executor=executor.execute
            )
            self._append(chat_id, "user", text)
            self._append(chat_id, "assistant", response)
            return f"_{active_label}_\n\n{response}"

        # Normal user: show welcome on first contact
        if not self._get_history(chat_id):
            welcome = self._welcome_message()
            self._append(chat_id, "assistant", welcome)
            return welcome

        executor = ToolExecutor(db, tenant_id)
        response = await self.agent.run(
            user_message=text,
            history=self._get_history(chat_id),
            tool_executor=executor.execute
        )
        self._append(chat_id, "user", text)
        self._append(chat_id, "assistant", response)
        return response

    def _resolve_admin_tenant(self, db, admin_chat_id: str, fallback_tenant_id: UUID) -> UUID:
        """
        Return the tenant the admin is currently operating as.

        Resolution order:
        1. Already switched to a specific tenant this session → use that
        2. BAKERY_OWNER_CHAT_ID is set (dedicated deployment) → use that owner's tenant
        3. Scan DB for first non-admin tenant (shared-bot deployment)
        4. Fall back to admin's own tenant (nothing else exists yet)
        """
        # 1. Already switched this session
        if admin_chat_id in self._admin_target:
            return self._admin_target[admin_chat_id]

        # 2. Dedicated deployment — owner chat_id explicitly configured
        if settings.BAKERY_OWNER_CHAT_ID:
            from app.services.tenant_service import TenantService
            tenant_svc = TenantService(db)
            owner_tenant = tenant_svc.get_or_create_tenant(settings.BAKERY_OWNER_CHAT_ID)
            self._admin_target[admin_chat_id] = owner_tenant.tenant_id
            return owner_tenant.tenant_id

        # 3. Shared deployment — use first non-admin tenant in DB
        from app.models import Tenant
        tenants = db.query(Tenant).filter(
            Tenant.chat_id != admin_chat_id
        ).order_by(Tenant.created_at).all()

        if tenants:
            self._admin_target[admin_chat_id] = tenants[0].tenant_id
            return tenants[0].tenant_id

        # 4. Nothing else exists — fall back to admin's own tenant
        return fallback_tenant_id

    def _admin_active_label(self, db, tenant_id: UUID) -> str:
        """Return a short label showing which bakery the admin is operating as."""
        from app.models import Tenant
        tenant = db.query(Tenant).filter(Tenant.tenant_id == tenant_id).first()
        if not tenant:
            return "Admin view"
        # If BAKERY_OWNER_CHAT_ID is set, this is a dedicated deployment — no need to show chat_id
        if settings.BAKERY_OWNER_CHAT_ID and tenant.chat_id == settings.BAKERY_OWNER_CHAT_ID:
            return "Admin view"
        return f"Admin view — bakery {tenant.chat_id}"

    def _handle_admin_switch(self, db, admin_chat_id: str, text: str) -> str:
        """
        Handle /switch <chat_id> command for admin.
        Switches which bakery tenant the admin is operating as.
        """
        parts = text.strip().split(maxsplit=1)
        if len(parts) < 2:
            # List available tenants
            from app.models import Tenant
            tenants = db.query(Tenant).filter(
                Tenant.chat_id != admin_chat_id
            ).order_by(Tenant.created_at).all()

            if not tenants:
                return "No bakery tenants found yet."

            lines = ["*Available bakeries:*\n"]
            for t in tenants:
                current = " ← current" if self._admin_target.get(admin_chat_id) == t.tenant_id else ""
                lines.append(f"• `{t.chat_id}`{current}")
            lines.append("\nUse `/switch <chat_id>` to switch.")
            return "\n".join(lines)

        target_chat_id = parts[1].strip()
        from app.models import Tenant
        tenant = db.query(Tenant).filter(Tenant.chat_id == target_chat_id).first()

        if not tenant:
            return f"❌ No bakery found with chat_id `{target_chat_id}`"

        self._admin_target[admin_chat_id] = tenant.tenant_id
        # Clear history so context doesn't bleed between bakeries
        self._history[admin_chat_id] = []
        return f"✅ Switched to bakery `{target_chat_id}`"

    def _welcome_message(self) -> str:
        """
        Welcome message shown to every new user on first contact.
        Explains all capabilities so they know what to ask for.
        """
        return (
            "👋 *Welcome to Bakery Operations Bot!*\n\n"
            "I'm your bakery assistant — just tell me what you need in plain language. "
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
            "• Send a WhatsApp order screenshot with caption *order*\n\n"

            "What would you like to start with?"
        )

    async def handle_image(
        self,
        db,
        tenant_id: UUID,
        chat_id: str,
        image_bytes: bytes,
        caption: str
    ) -> Optional[str]:
        """
        Process an image message and return the agent's response.

        Args:
            db: Database session
            tenant_id: Tenant UUID
            chat_id: Unique identifier for this conversation
            image_bytes: Raw image bytes
            caption: Caption provided by the user (used to determine image type)

        Returns:
            str: Response to send back to the user, or None if caption is missing
        """
        caption_lower = caption.lower()

        if any(w in caption_lower for w in ["receipt", "payment", "paid", "bill"]):
            result = await self.image_service.process_receipt_image(image_bytes)
            image_type = "receipt"
        elif any(w in caption_lower for w in ["recipe", "ingredients", "formula"]):
            result = await self.image_service.process_recipe_image(image_bytes)
            image_type = "recipe"
        elif any(w in caption_lower for w in ["order", "whatsapp", "message", "sms"]):
            result = await self.image_service.process_order_image(image_bytes)
            image_type = "order"
        else:
            # No caption - caller should ask the user to add one
            return None

        if "error" in result:
            return f"⚠️ {result['error']}"

        summary = self._summarise_image_result(image_type, result)
        logger.info(f"Image summary ({image_type}): {summary[:200]}")

        executor = ToolExecutor(db, tenant_id)
        response = await self.agent.run(
            user_message=summary,
            history=self._get_history(chat_id),
            tool_executor=executor.execute
        )
        self._append(chat_id, "user", f"[Image: {image_type}] {summary}")
        self._append(chat_id, "assistant", response)
        return response

    async def close(self):
        await self.agent.close()

    # ── Image summarisation ────────────────────────────────────────────────

    def _summarise_image_result(self, image_type: str, result: dict) -> str:
        """
        Convert extracted image data into a natural language instruction
        that the agent can act on directly.
        """
        if image_type == "recipe":
            return self._summarise_recipe(result)
        elif image_type == "receipt":
            return self._summarise_receipt(result)
        elif image_type == "order":
            return self._summarise_order(result)
        return f"I scanned a {image_type} image: {result}"

    def _summarise_recipe(self, result: dict) -> str:
        name = result.get("name", "Unknown")
        yield_per_batch = result.get("yield_per_batch") or 1
        ingredients = result.get("ingredients", [])
        packaging = result.get("packaging", [])

        lines = ["I scanned a recipe image. Please create this recipe:"]
        lines.append(f"Name: {name}")
        lines.append(f"Yield per batch: {yield_per_batch}")
        if ingredients:
            lines.append("Ingredients:")
            for ing in ingredients:
                lines.append(f"  - {ing.get('item_name')}: {ing.get('quantity')} {ing.get('unit', 'pcs')}")
        if packaging:
            lines.append("Packaging:")
            for pkg in packaging:
                lines.append(f"  - {pkg.get('item_name')}: {pkg.get('quantity')} {pkg.get('unit', 'pcs')}")
        lines.append(
            "Create the recipe and add all components. "
            "For any missing inventory items, add them with cost 0 so the user can update later."
        )
        return "\n".join(lines)

    def _summarise_receipt(self, result: dict) -> str:
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
