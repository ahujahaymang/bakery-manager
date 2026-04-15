"""
Unit tests for ConversationService.

Tests conversation state management, context data preservation,
and state transitions.
"""

import pytest
from datetime import datetime, timedelta
from uuid import uuid4
from app.services.conversation_service import ConversationService, ConversationState, ConversationContext


class TestConversationService:
    """Test suite for ConversationService class."""
    
    def setup_method(self):
        """Set up test fixtures before each test."""
        self.service = ConversationService()
        self.tenant_id = uuid4()
        self.chat_id = "test_chat_123"
    
    def test_get_context_creates_new_context(self):
        """Test that get_context creates a new context if none exists."""
        # Act
        context = self.service.get_context(self.tenant_id, self.chat_id)
        
        # Assert
        assert context is not None
        assert context.state == ConversationState.IDLE
        assert context.context_data == {}
        assert context.pending_action is None
    
    def test_set_state_updates_context(self):
        """Test that set_state updates the conversation state."""
        # Arrange
        context_data = {'customer_name': 'Priya'}
        # Create context first
        self.service.get_context(self.tenant_id, self.chat_id)
        
        # Act
        self.service.set_state(
            chat_id=self.chat_id,
            state=ConversationState.AWAITING_CUSTOMER_PHONE,
            pending_action="create_order",
            context_data=context_data
        )
        
        # Assert
        context = self.service.get_context(self.tenant_id, self.chat_id)
        assert context.state == ConversationState.AWAITING_CUSTOMER_PHONE
        assert context.pending_action == "create_order"
        assert context.context_data == context_data
    
    def test_set_state_preserves_context_data(self):
        """Test that context data is preserved across state changes."""
        # Arrange
        initial_data = {'customer_name': 'Priya', 'items': [{'recipe': 'cake'}]}
        # Create context first
        self.service.get_context(self.tenant_id, self.chat_id)
        self.service.set_state(
            chat_id=self.chat_id,
            state=ConversationState.AWAITING_CUSTOMER_PHONE,
            context_data=initial_data
        )
        
        # Act - transition to new state
        updated_data = {'customer_name': 'Priya', 'items': [{'recipe': 'cake'}], 'phone': '9876543210'}
        self.service.set_state(
            chat_id=self.chat_id,
            state=ConversationState.AWAITING_DELIVERY_DATE,
            context_data=updated_data
        )
        
        # Assert
        context = self.service.get_context(self.tenant_id, self.chat_id)
        assert context.state == ConversationState.AWAITING_DELIVERY_DATE
        assert context.context_data['customer_name'] == 'Priya'
        assert context.context_data['phone'] == '9876543210'
        assert len(context.context_data['items']) == 1
    
    def test_reset_context_clears_state(self):
        """Test that reset_context clears the conversation state."""
        # Arrange
        self.service.get_context(self.tenant_id, self.chat_id)
        self.service.set_state(
            chat_id=self.chat_id,
            state=ConversationState.AWAITING_CUSTOMER_PHONE,
            context_data={'customer_name': 'Priya'}
        )
        
        # Act
        self.service.reset_context(self.chat_id)
        
        # Assert
        context = self.service.get_context(self.tenant_id, self.chat_id)
        assert context.state == ConversationState.IDLE
        assert context.context_data == {}
        assert context.pending_action is None
    
    def test_context_expires_after_timeout(self):
        """Test that context expires after the timeout period."""
        # Arrange
        self.service.get_context(self.tenant_id, self.chat_id)
        self.service.set_state(
            chat_id=self.chat_id,
            state=ConversationState.AWAITING_CUSTOMER_PHONE,
            context_data={'customer_name': 'Priya'}
        )
        
        # Manually set expires_at to past
        context = self.service._conversations[self.chat_id]
        context.expires_at = datetime.utcnow() - timedelta(minutes=1)
        
        # Act
        retrieved_context = self.service.get_context(self.tenant_id, self.chat_id)
        
        # Assert - should return new IDLE context
        assert retrieved_context.state == ConversationState.IDLE
        assert retrieved_context.context_data == {}
    
    def test_multiple_chat_contexts_isolated(self):
        """Test that contexts for different chats are isolated."""
        # Arrange
        chat_id_1 = "chat_1"
        chat_id_2 = "chat_2"
        
        # Act
        self.service.get_context(self.tenant_id, chat_id_1)
        self.service.set_state(
            chat_id=chat_id_1,
            state=ConversationState.AWAITING_CUSTOMER_PHONE,
            context_data={'customer_name': 'Priya'}
        )
        
        self.service.get_context(self.tenant_id, chat_id_2)
        self.service.set_state(
            chat_id=chat_id_2,
            state=ConversationState.AWAITING_DELIVERY_DATE,
            context_data={'customer_name': 'Raj'}
        )
        
        # Assert
        context_1 = self.service.get_context(self.tenant_id, chat_id_1)
        context_2 = self.service.get_context(self.tenant_id, chat_id_2)
        
        assert context_1.state == ConversationState.AWAITING_CUSTOMER_PHONE
        assert context_1.context_data['customer_name'] == 'Priya'
        
        assert context_2.state == ConversationState.AWAITING_DELIVERY_DATE
        assert context_2.context_data['customer_name'] == 'Raj'
    
    def test_set_state_extends_expiry(self):
        """Test that set_state extends the expiry timestamp."""
        # Arrange
        self.service.get_context(self.tenant_id, self.chat_id)
        self.service.set_state(
            chat_id=self.chat_id,
            state=ConversationState.AWAITING_CUSTOMER_PHONE
        )
        
        first_expiry = self.service._conversations[self.chat_id].expires_at
        
        # Act - wait a moment and update state
        import time
        time.sleep(0.1)
        
        self.service.set_state(
            chat_id=self.chat_id,
            state=ConversationState.AWAITING_DELIVERY_DATE
        )
        
        second_expiry = self.service._conversations[self.chat_id].expires_at
        
        # Assert
        assert second_expiry > first_expiry
    
    def test_context_data_update_not_replace(self):
        """Test that context data is updated, not replaced."""
        # Arrange
        self.service.get_context(self.tenant_id, self.chat_id)
        self.service.set_state(
            chat_id=self.chat_id,
            state=ConversationState.AWAITING_CUSTOMER_PHONE,
            context_data={'customer_name': 'Priya'}
        )
        
        # Act - add more data
        self.service.set_state(
            chat_id=self.chat_id,
            state=ConversationState.AWAITING_DELIVERY_DATE,
            context_data={'phone': '9876543210'}
        )
        
        # Assert - both fields should be present
        context = self.service.get_context(self.tenant_id, self.chat_id)
        assert context.context_data['customer_name'] == 'Priya'
        assert context.context_data['phone'] == '9876543210'
    
    def test_pending_action_preserved(self):
        """Test that pending_action is preserved across get_context calls."""
        # Arrange
        self.service.get_context(self.tenant_id, self.chat_id)
        self.service.set_state(
            chat_id=self.chat_id,
            state=ConversationState.AWAITING_CUSTOMER_DISAMBIGUATION,
            pending_action="create_order",
            context_data={'order_data': {}}
        )
        
        # Act
        context1 = self.service.get_context(self.tenant_id, self.chat_id)
        context2 = self.service.get_context(self.tenant_id, self.chat_id)
        
        # Assert
        assert context1.pending_action == "create_order"
        assert context2.pending_action == "create_order"
    
    def test_order_data_preserved_through_disambiguation(self):
        """Test that order data is preserved through customer disambiguation."""
        # Arrange
        order_data = {
            'customer_identifier': 'Priya',
            'delivery_date': '',
            'items': [
                {'recipe_name': 'chocolate cake', 'quantity': 2, 'selling_price': 500.0}
            ]
        }
        
        # Act - Set disambiguation state with order data
        self.service.get_context(self.tenant_id, self.chat_id)
        self.service.set_state(
            chat_id=self.chat_id,
            state=ConversationState.AWAITING_CUSTOMER_DISAMBIGUATION,
            pending_action="create_order",
            context_data={'order_data': order_data}
        )
        
        # Retrieve context
        context = self.service.get_context(self.tenant_id, self.chat_id)
        
        # Assert
        assert context.context_data['order_data']['items'][0]['recipe_name'] == 'chocolate cake'
        assert context.context_data['order_data']['items'][0]['quantity'] == 2
        assert context.context_data['order_data']['items'][0]['selling_price'] == 500.0
