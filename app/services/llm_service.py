"""
LLM Service for intent detection and entity extraction.

This service uses an LLM to classify user intents and extract entities
from natural language messages for the Bakery Operations Bot.
"""

import json
import logging
from typing import Dict, Any, Optional
from dataclasses import dataclass
from enum import Enum

from app.llm_client import LLMClient

logger = logging.getLogger(__name__)


class Intent(str, Enum):
    """Supported intent types for the bakery bot."""
    CREATE_CUSTOMER = "create_customer"
    GET_CUSTOMER = "get_customer"
    LIST_CUSTOMERS = "list_customers"
    ADD_INVENTORY = "add_inventory"
    UPDATE_INVENTORY = "update_inventory"
    CHECK_STOCK = "check_stock"
    LIST_INVENTORY = "list_inventory"
    CREATE_RECIPE = "create_recipe"
    ADD_RECIPE_COMPONENT = "add_recipe_component"
    CALCULATE_RECIPE_COST = "calculate_recipe_cost"
    CREATE_ORDER = "create_order"
    MARK_DELIVERED = "mark_delivered"
    UPCOMING_ORDERS = "upcoming_orders"
    UNPAID_ORDERS = "unpaid_orders"
    RECORD_PAYMENT = "record_payment"
    PAYMENT_HISTORY = "payment_history"
    WEEKLY_PROFIT = "weekly_profit"
    UNKNOWN = "unknown"


@dataclass
class IntentResult:
    """Result of intent detection and entity extraction."""
    intent: Intent
    entities: Dict[str, Any]
    confidence: float
    raw_message: str


class LLMService:
    """
    Service for LLM-based intent detection and entity extraction.
    
    Uses an LLM to classify user messages into intents and extract
    relevant entities for processing by business logic services.
    """
    
    def __init__(self, llm_client: Optional[LLMClient] = None):
        """
        Initialize LLMService.
        
        Args:
            llm_client: Optional LLMClient instance (creates new one if not provided)
        """
        self.llm_client = llm_client or LLMClient()
        self.confidence_threshold = 0.7
    
    async def detect_intent(self, message: str) -> IntentResult:
        """
        Detect intent and extract entities from a user message.
        
        Sends the message to the LLM with a classification prompt, parses
        the response into an IntentResult object with intent type, entities,
        and confidence score.
        
        Args:
            message: User's natural language message
        
        Returns:
            IntentResult: Detected intent, extracted entities, and confidence
        
        Requirements:
            - 22.1: Classify intent (create_customer, add_inventory, create_order, etc.)
            - 22.2: Extract entities (names, quantities, dates, prices)
            - 22.4: Return clarification request if confidence is low
        """
        if not message or not message.strip():
            return IntentResult(
                intent=Intent.UNKNOWN,
                entities={},
                confidence=0.0,
                raw_message=message
            )
        
        # Create the intent classification prompt
        system_prompt = self._create_system_prompt()
        user_prompt = f"User message: {message.strip()}"
        
        try:
            # Call LLM for intent classification
            response = await self.llm_client.call_llm(
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt}
                ],
                temperature=0.3,  # Lower temperature for more consistent classification
                max_tokens=500
            )
            
            # Parse LLM response
            content = response.get("choices", [{}])[0].get("message", {}).get("content", "")
            
            if not content:
                logger.warning("Empty response from LLM")
                return IntentResult(
                    intent=Intent.UNKNOWN,
                    entities={},
                    confidence=0.0,
                    raw_message=message
                )
            
            # Parse JSON response from LLM
            # Handle markdown code blocks if present
            content = content.strip()
            if content.startswith("```json"):
                content = content[7:]  # Remove ```json
            if content.startswith("```"):
                content = content[3:]  # Remove ```
            if content.endswith("```"):
                content = content[:-3]  # Remove trailing ```
            content = content.strip()
            
            try:
                result_data = json.loads(content)
            except json.JSONDecodeError as e:
                logger.error(f"Failed to parse LLM response as JSON: {e}")
                logger.debug(f"LLM response content: {content}")
                return IntentResult(
                    intent=Intent.UNKNOWN,
                    entities={},
                    confidence=0.0,
                    raw_message=message
                )
            
            # Extract intent, entities, and confidence
            intent_str = result_data.get("intent", "unknown")
            entities = result_data.get("entities", {})
            confidence = float(result_data.get("confidence", 0.0))
            
            # Validate intent
            try:
                intent = Intent(intent_str)
            except ValueError:
                logger.warning(f"Unknown intent from LLM: {intent_str}")
                intent = Intent.UNKNOWN
            
            return IntentResult(
                intent=intent,
                entities=entities,
                confidence=confidence,
                raw_message=message
            )
        
        except Exception as e:
            logger.error(f"Error during intent detection: {e}")
            return IntentResult(
                intent=Intent.UNKNOWN,
                entities={},
                confidence=0.0,
                raw_message=message
            )
    
    def _create_system_prompt(self) -> str:
        """
        Create the system prompt for intent classification.
        
        Returns:
            str: System prompt with instructions and examples
        """
        from datetime import datetime, date
        
        # Get current date and time for temporal awareness
        now = datetime.now()
        today = date.today()
        current_date_str = today.isoformat()
        current_datetime_str = now.strftime("%Y-%m-%d %H:%M:%S")
        
        return f"""You are an intent classifier for a bakery operations management bot. Your job is to classify user messages into specific intents and extract relevant entities.

**CURRENT DATE AND TIME:**
- Today's date: {current_date_str}
- Current time: {current_datetime_str}
- Use this information when interpreting relative dates like "tomorrow", "next week", "today", etc.

**IMPORTANT:** When extracting dates, always convert relative dates to absolute dates in YYYY-MM-DD format using the current date above.

**Available Intents:**
- create_customer: Add a new customer (needs: name, phone)
- get_customer: Search for a customer (needs: name or phone)
- list_customers: List all customers
- add_inventory: Add a new inventory item (needs: name, category, quantity, unit, cost_per_unit)
- update_inventory: Update inventory quantity or cost (needs: name, quantity or cost_per_unit)
- check_stock: Check stock level for an item (needs: name)
- list_inventory: List all inventory items
- create_recipe: Create a new recipe (needs: name, yield_per_batch)
- add_recipe_component: Add ingredient/packaging to recipe (needs: recipe_name, item_name, quantity, component_type)
- calculate_recipe_cost: Calculate recipe cost (needs: recipe_name)
- create_order: Create a new order (needs: customer_identifier, delivery_date, items with recipe_name, quantity, selling_price)
- mark_delivered: Mark order as delivered (needs: order_id)
- upcoming_orders: View upcoming pending orders
- unpaid_orders: View orders with unpaid balance
- record_payment: Record a payment (needs: order_id, amount, method)
- payment_history: View payment history (optional: start_date, end_date)
- weekly_profit: Calculate weekly profit
- unknown: Cannot determine intent

**Entity Types:**
- name: Customer or item name (string)
- phone: Phone number (string with digits)
- category: "ingredient" or "packaging"
- quantity: Numeric value (can be decimal)
- unit: "kg", "g", "litre", "ml", or "pcs"
- cost_per_unit: Numeric value (decimal)
- recipe_name: Recipe name (string)
- item_name: Inventory item name (string)
- yield_per_batch: Integer value
- component_type: "ingredient" or "packaging"
- customer_identifier: Name or phone for customer lookup
- delivery_date: Date in YYYY-MM-DD format
- items: Array of order items with recipe_name, quantity, selling_price
- order_id: UUID string
- amount: Numeric value (decimal)
- method: "Cash", "Paytm", or "Bank Transfer"
- start_date: Date in YYYY-MM-DD format
- end_date: Date in YYYY-MM-DD format

**Response Format:**
Return ONLY a JSON object with this structure:
{{
  "intent": "intent_name",
  "entities": {{
    "entity_name": "value",
    ...
  }},
  "confidence": 0.95
}}

**Examples:**

User: "Add customer Priya with phone 9876543210"
{{
  "intent": "create_customer",
  "entities": {{
    "name": "Priya",
    "phone": "9876543210"
  }},
  "confidence": 0.95
}}

User: "Add 5kg flour at 40 rupees per kg"
{{
  "intent": "add_inventory",
  "entities": {{
    "name": "flour",
    "category": "ingredient",
    "quantity": 5,
    "unit": "kg",
    "cost_per_unit": 40
  }},
  "confidence": 0.9
}}

User: "Create order for Priya, 2 chocolate cakes at 500 each, deliver tomorrow"
{{
  "intent": "create_order",
  "entities": {{
    "customer_identifier": "Priya",
    "items": [
      {{
        "recipe_name": "chocolate cake",
        "quantity": 2,
        "selling_price": 500
      }}
    ],
    "delivery_date": "2026-04-16"
  }},
  "confidence": 0.85
}}

Note: "tomorrow" was converted to the actual date based on current date.

User: "Show me this week's profit"
{{
  "intent": "weekly_profit",
  "entities": {{}},
  "confidence": 0.95
}}

**Important Rules:**
1. Always return valid JSON
2. Set confidence between 0.0 and 1.0
3. If unsure, use "unknown" intent with low confidence
4. Extract all relevant entities from the message
5. Normalize entity values (lowercase for categories, proper format for dates)
6. **CRITICAL:** For dates like "tomorrow", "next week", "today", calculate the actual date in YYYY-MM-DD format using the current date provided above
7. Infer missing information when obvious (e.g., "flour" is likely an ingredient)

Now classify the following user message:"""
    
    def is_confident(self, result: IntentResult) -> bool:
        """
        Check if the intent detection confidence meets the threshold.
        
        Args:
            result: IntentResult to check
        
        Returns:
            bool: True if confidence >= threshold, False otherwise
        """
        return result.confidence >= self.confidence_threshold
    
    def get_clarification_message(self, result: IntentResult) -> str:
        """
        Generate a clarification message for low-confidence results.
        
        Args:
            result: IntentResult with low confidence
        
        Returns:
            str: User-friendly clarification request
        """
        if result.intent == Intent.UNKNOWN:
            return (
                "I'm not sure what you're asking for. Here are some things I can help with:\n"
                "- Add or search customers\n"
                "- Manage inventory items\n"
                "- Create recipes and calculate costs\n"
                "- Create and track orders\n"
                "- Record payments\n"
                "- View reports and profit\n\n"
                "Could you please rephrase your request?"
            )
        else:
            return (
                f"I think you want to {result.intent.value.replace('_', ' ')}, "
                "but I'm not completely sure. Could you please provide more details?"
            )
    
    async def close(self):
        """Close the LLM client connection."""
        await self.llm_client.close()
