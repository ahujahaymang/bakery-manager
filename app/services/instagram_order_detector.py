"""
Instagram Order Detector.

Reads a DM conversation thread and uses the LLM to determine if an order
was confirmed. Returns structured order data if found, None otherwise.

This is intentionally separate from the main agent — it's a focused
extraction task, not a conversational agent.
"""

import json
import logging
from dataclasses import dataclass
from typing import List, Optional

logger = logging.getLogger(__name__)


@dataclass
class DetectedOrder:
    """Order details extracted from an Instagram DM conversation."""
    customer_name: Optional[str]
    customer_instagram_handle: str
    items: List[dict]           # [{name, quantity, price}]
    delivery_date: Optional[str]
    delivery_address: Optional[str]
    raw_conversation: str
    confidence: float           # 0.0 - 1.0


class InstagramOrderDetector:
    """
    Detects confirmed orders in Instagram DM conversations using the LLM.

    Called after a conversation thread goes quiet (30 min inactivity).
    Only creates an order if the LLM is confident one was confirmed.
    """

    def __init__(self, llm_service):
        self.llm_service = llm_service

    async def detect_order(
        self,
        messages: List[dict],
        instagram_handle: str,
    ) -> Optional[DetectedOrder]:
        """
        Analyse a DM conversation and extract order details if confirmed.

        Args:
            messages: List of {sender, text, timestamp} dicts, oldest first
            instagram_handle: The customer's Instagram handle

        Returns:
            DetectedOrder if an order was confirmed, None otherwise
        """
        if not messages:
            return None

        conversation = self._format_conversation(messages)
        prompt = self._build_prompt(conversation, instagram_handle)

        try:
            result = await self.llm_service.extract_structured_data(prompt)
        except Exception as e:
            logger.error(f"Order detection failed: {e}", exc_info=True)
            return None

        if not result.get("order_confirmed"):
            return None

        confidence = float(result.get("confidence", 0.0))
        if confidence < 0.7:
            logger.info(f"Order detected but low confidence ({confidence:.0%}), skipping")
            return None

        return DetectedOrder(
            customer_name=result.get("customer_name"),
            customer_instagram_handle=instagram_handle,
            items=result.get("items", []),
            delivery_date=result.get("delivery_date"),
            delivery_address=result.get("delivery_address"),
            raw_conversation=conversation,
            confidence=confidence,
        )

    def _format_conversation(self, messages: List[dict]) -> str:
        """Format messages into a readable conversation string."""
        lines = []
        for msg in messages:
            sender = msg.get("sender", "unknown")
            text = msg.get("text", "")
            lines.append(f"{sender}: {text}")
        return "\n".join(lines)

    def _build_prompt(self, conversation: str, instagram_handle: str) -> str:
        from datetime import date
        today = date.today().isoformat()

        return f"""You are analysing an Instagram DM conversation between a home baker and their customer.

Today's date: {today}
Customer's Instagram handle: @{instagram_handle}

Conversation:
{conversation}

Determine if a specific order was CONFIRMED in this conversation (not just enquired about).
An order is confirmed when the customer explicitly agrees to buy specific items at a specific price.

Return ONLY a JSON object:
{{
  "order_confirmed": true or false,
  "confidence": 0.0 to 1.0,
  "customer_name": "<name if mentioned, else null>",
  "items": [
    {{"name": "<item name>", "quantity": <number>, "price": <number or null>}}
  ],
  "delivery_date": "<YYYY-MM-DD or null>",
  "delivery_address": "<address if mentioned, else null>",
  "notes": "<any special instructions>"
}}

If no order was confirmed, set order_confirmed to false and confidence to 0.
Only set order_confirmed to true if you are certain an order was agreed upon."""
