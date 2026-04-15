# Conversational State Management Implementation

## Status: In Progress

## Completed
1. ✅ Created `ConversationService` class with state management
2. ✅ Added conversation states: IDLE, AWAITING_CUSTOMER_PHONE, AWAITING_DELIVERY_DATE, AWAITING_RECIPE_DISAMBIGUATION
3. ✅ Integrated ConversationService into TelegramBotListener
4. ✅ Updated handle_message to check conversation state before processing new intents
5. ✅ Added conversation continuation handler skeleton

## Remaining Work

### 1. Implement Conversation State Handlers
Need to add these methods to `telegram_listener.py`:

```python
async def handle_awaiting_customer_phone(self, db, tenant_id, chat_id, text, conv_context):
    """Handle phone number input for customer creation."""
    # Extract phone from text
    # Create customer with stored name and new phone
    # Resume order creation with stored order data
    # Reset conversation state
    pass

async def handle_awaiting_delivery_date(self, db, tenant_id, chat_id, text, conv_context):
    """Handle delivery date input for order creation."""
    # Parse date from text using LLM
    # Resume order creation with stored order data + date
    # Reset conversation state
    pass

async def handle_awaiting_recipe_disambiguation(self, db, tenant_id, chat_id, text, conv_context):
    """Handle recipe selection from multiple matches."""
    # Parse user's choice (number or name)
    # Resume order creation with selected recipe
    # Reset conversation state
    pass
```

### 2. Update handle_create_order Method
Modify to handle:
- **Missing customer**: Set state to AWAITING_CUSTOMER_PHONE, store order data
- **Ambiguous recipe**: Set state to AWAITING_RECIPE_DISAMBIGUATION, show options
- **Missing delivery date**: Already handled, but integrate with conversation state

### 3. Add Recipe Disambiguation Logic
In `OrderService.create_order`:
- When resolving recipe names, check for multiple fuzzy matches
- Return list of matching recipes if ambiguous
- Let telegram_listener handle the disambiguation conversation

### 4. Update All route_intent Calls
The signature changed from:
```python
await self.route_intent(db, tenant_id, intent_result)
```
To:
```python
await self.route_intent(db, tenant_id, chat_id, intent_result)
```

Need to update all calls in the route_intent method itself (recursive calls).

### 5. Add Conversation Cleanup
Add periodic cleanup of expired conversations:
```python
async def cleanup_expired_conversations(self):
    """Periodically clean up expired conversation contexts."""
    while True:
        await asyncio.sleep(60)  # Every minute
        self.conversation_service.cleanup_expired()
```

Start this in the `start()` method.

### 6. Testing Scenarios

#### Scenario 1: Missing Customer
```
User: "Raj ordered a cake for tomorrow at 1500"
Bot: "Customer 'Raj' not found. Please provide Raj's phone number."
User: "9876543210"
Bot: "✅ Customer added! Order created for Raj..."
```

#### Scenario 2: Ambiguous Recipe
```
User: "Sanjay ordered chocolate cake for April 20 at 1800"
Bot: "I found multiple recipes matching 'chocolate cake':
     1. Chocolate Mousse Cake
     2. Chocolate Cheese Cake
     Which one should I use? Reply with the number or full name."
User: "1"
Bot: "✅ Order created with Chocolate Mousse Cake..."
```

#### Scenario 3: Missing Delivery Date
```
User: "Anuj ordered blueberry cake at 2100"
Bot: "📅 When should this order be delivered?
     Customer: Anuj
     Items: blueberry cake x1 @ ₹2100
     Please provide the delivery date."
User: "May 5"
Bot: "✅ Order created! Delivery: 2026-05-05..."
```

## Implementation Priority
1. **High**: handle_awaiting_customer_phone (most common scenario)
2. **High**: handle_awaiting_recipe_disambiguation (requested feature)
3. **Medium**: handle_awaiting_delivery_date (already partially handled)
4. **Low**: Conversation cleanup (nice to have)

## Files to Modify
- `app/telegram_listener.py` - Add conversation handlers
- `app/services/order_service.py` - Add recipe fuzzy matching
- `app/services/recipe_service.py` - Add recipe search method

## Next Steps
1. Implement the three conversation state handlers
2. Update handle_create_order to set conversation states
3. Add recipe fuzzy matching to RecipeService
4. Test all three scenarios
5. Commit and push changes
