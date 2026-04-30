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

        Args:
            db: Database session
            tenant_id: Tenant UUID
            chat_id: Unique identifier for this conversation
            text: User's message

        Returns:
            str: Response to send back to the user
        """
        executor = ToolExecutor(db, tenant_id)
        response = await self.agent.run(
            user_message=text,
            history=self._get_history(chat_id),
            tool_executor=executor.execute
        )
        self._append(chat_id, "user", text)
        self._append(chat_id, "assistant", response)
        return response

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
