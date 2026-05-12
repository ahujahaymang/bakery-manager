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
    LIST_RECIPES = "list_recipes"
    GET_RECIPE = "get_recipe"
    UPDATE_RECIPE = "update_recipe"
    REMOVE_RECIPE_COMPONENT = "remove_recipe_component"
    UPDATE_RECIPE_COMPONENT = "update_recipe_component"
    DELETE_RECIPE = "delete_recipe"
    CREATE_ORDER = "create_order"
    CANCEL_ORDER = "cancel_order"
    DELETE_ORDER = "delete_order"
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
            try:
                result_data = self._parse_json_response(content)
            except (ValueError, Exception) as e:
                logger.error(f"Failed to parse LLM response as JSON: {e}")
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
- list_inventory: List inventory items (optional: category filter - "ingredient" or "packaging")
- create_recipe: Create a new recipe (needs: name, yield_per_batch)
- add_recipe_component: Add ingredient/packaging to recipe (needs: recipe_name, item_name, quantity, component_type)
- calculate_recipe_cost: Calculate recipe cost (needs: recipe_name)
- list_recipes: List all recipes
- get_recipe: View a specific recipe with all components (needs: recipe_name)
- update_recipe: Rename a recipe or change its yield (needs: recipe_name, optional: new_name, new_yield)
- remove_recipe_component: Remove an ingredient or packaging from a recipe (needs: recipe_name, item_name)
- update_recipe_component: Change the quantity of a component in a recipe (needs: recipe_name, item_name, quantity)
- delete_recipe: Delete a recipe entirely (needs: recipe_name)
- create_order: Create a new order (needs: customer_identifier, delivery_date, items with recipe_name, quantity, selling_price)
- cancel_order: Mark order as cancelled (customer cancelled, keep for analysis) (needs: order_id or customer_identifier with delivery_date)
- delete_order: Permanently delete order (entered incorrectly) (needs: order_id or customer_identifier with delivery_date)
- mark_delivered: Mark order as delivered (needs: order_id)
- upcoming_orders: View upcoming pending orders (optional: filter - "paid", "unpaid", "delivered", "pending")
- unpaid_orders: View orders with unpaid balance (same as upcoming_orders with filter="unpaid")
- record_payment: Record a payment (needs: order_identifier (can be order_id UUID or customer name/phone), amount, method)
- payment_history: View payment history (optional: start_date, end_date)
- weekly_profit: Calculate weekly profit
- unknown: Cannot determine intent

**Natural Language Understanding:**
- "show paid orders" → upcoming_orders with filter="paid"
- "show unpaid orders" → unpaid_orders OR upcoming_orders with filter="unpaid"
- "show inventory" → list_inventory (all items)
- "show ingredients" → list_inventory with category="ingredient"
- "show pantry" → list_inventory with category="ingredient"
- "show packaging" → list_inventory with category="packaging"
- "show recipes" or "list recipes" → list_recipes
- "show recipe X" or "view recipe X" → get_recipe with recipe_name
- "rename recipe X to Y" → update_recipe with recipe_name and new_name
- "change yield of X to 12" → update_recipe with recipe_name and new_yield
- "remove flour from X recipe" → remove_recipe_component with recipe_name and item_name
- "update flour to 200g in X recipe" → update_recipe_component with recipe_name, item_name, quantity
- "delete recipe X" → delete_recipe with recipe_name
- "order cancelled" or "customer cancelled" → cancel_order (marks as cancelled, keeps in DB)
- "delete order" or "remove order" → delete_order (permanently deletes, for mistakes)
- "Priya's order got cancelled" → cancel_order with customer_identifier
- "delete order for Priya tomorrow" → delete_order with customer_identifier and delivery_date

**Entity Types:**
- name: Customer or item name (string)
- phone: Phone number (string with digits)
- category: "ingredient" or "packaging" (for inventory filtering)
- filter: Order status filter - "paid", "unpaid", "delivered", "pending"
- quantity: Numeric value (can be decimal)
- unit: "kg", "g", "litre", "ml", or "pcs"
- cost_per_unit: Numeric value (decimal)
- recipe_name: Recipe name (string)
- item_name: Inventory item name (string)
- yield_per_batch: Integer value
- new_name: New name when renaming (string)
- new_yield: New yield per batch when updating (integer)
- component_type: "ingredient" or "packaging"
- customer_identifier: Name or phone for customer lookup
- delivery_date: Date in YYYY-MM-DD format
- items: Array of order items with recipe_name, quantity, selling_price
- order_id: UUID string
- order_identifier: Order ID (UUID) or customer name/phone for finding unpaid orders
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

User: "Priya order 10 cupcakes at 50 each, deliver April 20"
{{
  "intent": "create_order",
  "entities": {{
    "customer_identifier": "Priya",
    "items": [
      {{
        "recipe_name": "cupcakes",
        "quantity": 10,
        "selling_price": 50
      }}
    ],
    "delivery_date": "2026-04-20"
  }},
  "confidence": 0.85
}}

Note: "April 20" was parsed as "2026-04-20" using the current year.

User: "Show me this week's profit"
{{
  "intent": "weekly_profit",
  "entities": {{}},
  "confidence": 0.95
}}

User: "Show paid orders"
{{
  "intent": "upcoming_orders",
  "entities": {{
    "filter": "paid"
  }},
  "confidence": 0.9
}}

User: "Show ingredients" OR "Show pantry"
{{
  "intent": "list_inventory",
  "entities": {{
    "category": "ingredient"
  }},
  "confidence": 0.95
}}

User: "Show packaging materials"
{{
  "intent": "list_inventory",
  "entities": {{
    "category": "packaging"
  }},
  "confidence": 0.95
}}

User: "Delete order for Priya tomorrow"
{{
  "intent": "delete_order",
  "entities": {{
    "customer_identifier": "Priya",
    "delivery_date": "2026-04-16"
  }},
  "confidence": 0.85
}}

User: "Cancel Raj's order" OR "Raj's order got cancelled"
{{
  "intent": "cancel_order",
  "entities": {{
    "customer_identifier": "Raj"
  }},
  "confidence": 0.85
}}

Note: cancel_order marks as cancelled (keeps in DB for analysis), delete_order permanently removes (for data entry mistakes).

**Important Rules:**
1. Always return valid JSON
2. Set confidence between 0.0 and 1.0
3. If unsure, use "unknown" intent with low confidence
4. Extract all relevant entities from the message
5. Normalize entity values (lowercase for categories, proper format for dates)
6. **CRITICAL:** For dates like "tomorrow", "next week", "today", calculate the actual date in YYYY-MM-DD format using the current date provided above
7. Infer missing information when obvious (e.g., "flour" is likely an ingredient)
8. **Natural Language Flexibility:** Understand variations like "show paid orders" (upcoming_orders with filter="paid"), "show ingredients" (list_inventory with category="ingredient")

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
    
    async def extract_structured_data_from_image(self, prompt: str, image_b64: str) -> Dict[str, Any]:
        """
        Extract structured data from an image using GPT-4o Vision.
        
        Sends the image directly to GPT-4o which handles handwriting,
        printed text, and screenshots natively.
        
        Args:
            prompt: Extraction instructions and JSON schema
            image_b64: Base64-encoded image string
        
        Returns:
            Dict with extracted data
        
        Raises:
            ValueError: If LLM response cannot be parsed
        """
        try:
            messages = [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:image/jpeg;base64,{image_b64}",
                                "detail": "auto"
                            }
                        },
                        {
                            "type": "text",
                            "text": prompt
                        }
                    ]
                }
            ]
            
            response = await self.llm_client.call_llm(
                messages=messages,
                temperature=0.2,
                max_tokens=4000  # catalog images need more tokens for 50+ products
            )
            
            content = response.get("choices", [{}])[0].get("message", {}).get("content", "")
            
            if not content:
                raise ValueError("Empty response from LLM")

            return self._parse_json_response(content)
        
        except Exception as e:
            logger.error(f"Error in vision extraction: {e}", exc_info=True)
            raise ValueError(f"Failed to process image: {str(e)}")
    
    async def extract_structured_data(self, prompt: str) -> Dict[str, Any]:
        """
        Extract structured data from text using LLM.
        
        Used by ImageService to parse OCR text into structured JSON.
        
        Args:
            prompt: Prompt with text to extract and JSON schema
        
        Returns:
            Dict with extracted data
        
        Raises:
            ValueError: If LLM response cannot be parsed
        """
        try:
            response = await self.llm_client.call_llm(
                messages=[
                    {"role": "user", "content": prompt}
                ],
                temperature=0.2,  # Low temperature for consistent extraction
                max_tokens=1000
            )
            
            content = response.get("choices", [{}])[0].get("message", {}).get("content", "")
            
            if not content:
                raise ValueError("Empty response from LLM")
            
            # Parse JSON
            try:
                return self._parse_json_response(content)
            except ValueError as e:
                raise ValueError(f"Failed to parse structured data: {str(e)}")
        
        except Exception as e:
            logger.error(f"Error extracting structured data: {e}", exc_info=True)
            raise ValueError(f"Failed to extract structured data: {str(e)}")
    
    async def close(self):
        """Close the LLM client connection."""
        await self.llm_client.close()

    def _parse_json_response(self, content: str) -> dict:
        """
        Robustly parse a JSON response from an LLM.

        Handles common LLM output issues:
        - Markdown code fences (```json ... ```)
        - Trailing commas before } or ]
        - Single-line // comments
        - Leading/trailing whitespace
        - Extracting the first {...} block if surrounded by prose
        """
        import re

        text = content.strip()

        # Strip markdown code fences
        text = re.sub(r'^```(?:json)?\s*', '', text)
        text = re.sub(r'\s*```$', '', text)
        text = text.strip()

        # Try direct parse first
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass

        # Extract the outermost {...} block (handles prose before/after JSON)
        match = re.search(r'\{.*\}', text, re.DOTALL)
        if match:
            candidate = match.group(0)
            try:
                return json.loads(candidate)
            except json.JSONDecodeError:
                # Remove trailing commas before } or ]
                cleaned = re.sub(r',\s*([}\]])', r'\1', candidate)
                # Remove // comments
                cleaned = re.sub(r'//[^\n]*', '', cleaned)
                try:
                    return json.loads(cleaned)
                except json.JSONDecodeError as e:
                    logger.error(f"JSON parse failed after cleanup: {e}")
                    logger.debug(f"Cleaned content: {cleaned[:500]}")
                    raise ValueError(f"Failed to parse image data: {str(e)}")

        raise ValueError(f"No JSON object found in response: {text[:200]}")
