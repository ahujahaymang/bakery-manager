"""
Conversation Service for managing multi-turn conversation state.

This service handles conversation context, pending actions, and state
management for seamless multi-turn interactions with users.
"""

import logging
from typing import Optional, Dict, Any
from datetime import datetime, timedelta
from dataclasses import dataclass, field
from enum import Enum
from uuid import UUID

logger = logging.getLogger(__name__)


class ConversationState(str, Enum):
    """Possible conversation states."""
    IDLE = "idle"
    AWAITING_CUSTOMER_PHONE = "awaiting_customer_phone"
    AWAITING_CUSTOMER_DISAMBIGUATION = "awaiting_customer_disambiguation"
    AWAITING_DELIVERY_DATE = "awaiting_delivery_date"
    AWAITING_RECIPE_DISAMBIGUATION = "awaiting_recipe_disambiguation"
    AWAITING_CONFIRMATION = "awaiting_confirmation"


@dataclass
class ConversationContext:
    """
    Conversation context for tracking multi-turn interactions.
    
    Stores the current state, pending action, and any data collected
    from previous messages in the conversation.
    """
    tenant_id: UUID
    chat_id: str
    state: ConversationState = ConversationState.IDLE
    pending_action: Optional[str] = None
    context_data: Dict[str, Any] = field(default_factory=dict)
    created_at: datetime = field(default_factory=datetime.utcnow)
    expires_at: datetime = field(default_factory=lambda: datetime.utcnow() + timedelta(minutes=5))
    
    def is_expired(self) -> bool:
        """Check if the conversation context has expired."""
        return datetime.utcnow() > self.expires_at
    
    def reset(self):
        """Reset the conversation to idle state."""
        self.state = ConversationState.IDLE
        self.pending_action = None
        self.context_data = {}
        self.created_at = datetime.utcnow()
        self.expires_at = datetime.utcnow() + timedelta(minutes=5)
    
    def extend_expiry(self, minutes: int = 5):
        """Extend the expiry time of the conversation."""
        self.expires_at = datetime.utcnow() + timedelta(minutes=minutes)


class ConversationService:
    """
    Service for managing conversation state across multiple messages.
    
    Handles context tracking, state transitions, and data persistence
    for multi-turn conversations. Uses in-memory storage with expiration.
    """
    
    def __init__(self):
        """Initialize ConversationService with in-memory storage."""
        # In-memory storage: {chat_id: ConversationContext}
        self._conversations: Dict[str, ConversationContext] = {}
    
    def get_context(self, tenant_id: UUID, chat_id: str) -> ConversationContext:
        """
        Get or create conversation context for a chat.
        
        Args:
            tenant_id: UUID of the tenant
            chat_id: Telegram chat ID
        
        Returns:
            ConversationContext: Current or new conversation context
        """
        # Check if context exists and is not expired
        if chat_id in self._conversations:
            context = self._conversations[chat_id]
            
            # Remove expired context
            if context.is_expired():
                logger.info(f"Conversation context expired for chat_id={chat_id}")
                del self._conversations[chat_id]
            else:
                # Extend expiry on access
                context.extend_expiry()
                return context
        
        # Create new context
        context = ConversationContext(
            tenant_id=tenant_id,
            chat_id=chat_id
        )
        self._conversations[chat_id] = context
        logger.info(f"Created new conversation context for chat_id={chat_id}")
        return context
    
    def set_state(
        self,
        chat_id: str,
        state: ConversationState,
        pending_action: Optional[str] = None,
        context_data: Optional[Dict[str, Any]] = None
    ):
        """
        Set conversation state and context data.
        
        Args:
            chat_id: Telegram chat ID
            state: New conversation state
            pending_action: Action waiting for completion
            context_data: Additional context data to store
        """
        if chat_id not in self._conversations:
            logger.warning(f"Attempted to set state for non-existent context: {chat_id}")
            return
        
        context = self._conversations[chat_id]
        context.state = state
        context.pending_action = pending_action
        
        if context_data:
            context.context_data.update(context_data)
        
        context.extend_expiry()
        logger.info(f"Set conversation state for chat_id={chat_id}: {state}")
    
    def clear_context(self, chat_id: str):
        """
        Clear conversation context for a chat.
        
        Args:
            chat_id: Telegram chat ID
        """
        if chat_id in self._conversations:
            del self._conversations[chat_id]
            logger.info(f"Cleared conversation context for chat_id={chat_id}")
    
    def reset_context(self, chat_id: str):
        """
        Reset conversation context to idle state.
        
        Args:
            chat_id: Telegram chat ID
        """
        if chat_id in self._conversations:
            self._conversations[chat_id].reset()
            logger.info(f"Reset conversation context for chat_id={chat_id}")
    
    def is_awaiting_response(self, chat_id: str) -> bool:
        """
        Check if conversation is awaiting a response from user.
        
        Args:
            chat_id: Telegram chat ID
        
        Returns:
            bool: True if awaiting response, False otherwise
        """
        if chat_id not in self._conversations:
            return False
        
        context = self._conversations[chat_id]
        return context.state != ConversationState.IDLE and not context.is_expired()
    
    def cleanup_expired(self):
        """Remove all expired conversation contexts."""
        expired_chats = [
            chat_id for chat_id, context in self._conversations.items()
            if context.is_expired()
        ]
        
        for chat_id in expired_chats:
            del self._conversations[chat_id]
            logger.info(f"Cleaned up expired context for chat_id={chat_id}")
        
        if expired_chats:
            logger.info(f"Cleaned up {len(expired_chats)} expired conversations")
