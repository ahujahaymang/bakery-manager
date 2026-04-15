"""
Error handling module for user-friendly error responses.

This module provides consistent error formatting and handling for all
error types in the Bakery Operations Bot.
"""

import logging
from typing import Dict, Any, Optional
from enum import Enum

logger = logging.getLogger(__name__)


class ErrorType(str, Enum):
    """Types of errors that can occur in the system."""
    VALIDATION_ERROR = "validation_error"
    NOT_FOUND_ERROR = "not_found_error"
    BUSINESS_LOGIC_ERROR = "business_logic_error"
    DATABASE_ERROR = "database_error"
    LLM_ERROR = "llm_error"
    TELEGRAM_ERROR = "telegram_error"
    SYSTEM_ERROR = "system_error"


class ErrorHandler:
    """
    Handler for formatting and managing errors.
    
    Provides user-friendly error messages without exposing internal details.
    """
    
    @staticmethod
    def format_error_response(
        error_type: ErrorType,
        message: str,
        details: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        Format an error into a consistent response structure.
        
        Creates a standardized error response that is user-friendly and
        does not expose internal system details or stack traces.
        
        Args:
            error_type: Type of error that occurred
            message: User-friendly error message
            details: Optional additional details (sanitized)
        
        Returns:
            Dict: Formatted error response
        
        Requirements:
            - 23.1: Return user-friendly error messages
            - 23.2: Never expose internal error details
            - 23.3: Never expose stack traces
            - 23.4: Provide actionable guidance when possible
            - 23.5: Use consistent error format
        """
        # Log the error for debugging (internal use only)
        logger.error(f"Error [{error_type}]: {message}")
        if details:
            logger.debug(f"Error details: {details}")
        
        # Return sanitized error response
        return {
            "success": False,
            "error_type": error_type.value,
            "message": message,
            "timestamp": None  # Will be set by caller if needed
        }
    
    @staticmethod
    def handle_validation_error(error: Exception) -> Dict[str, Any]:
        """
        Handle validation errors with user-friendly messages.
        
        Args:
            error: The validation error exception
        
        Returns:
            Dict: Formatted error response
        """
        message = str(error)
        
        # Ensure the message is user-friendly
        if not message or "internal" in message.lower() or "exception" in message.lower():
            message = "The information you provided is not valid. Please check and try again."
        
        return ErrorHandler.format_error_response(
            error_type=ErrorType.VALIDATION_ERROR,
            message=message
        )
    
    @staticmethod
    def handle_not_found_error(error: Exception, resource_type: str = "item") -> Dict[str, Any]:
        """
        Handle not found errors with user-friendly messages.
        
        Args:
            error: The not found error exception
            resource_type: Type of resource that was not found
        
        Returns:
            Dict: Formatted error response
        """
        message = str(error)
        
        # Provide helpful guidance
        if not message or "not found" not in message.lower():
            message = f"The {resource_type} you're looking for was not found. Please check the details and try again."
        
        return ErrorHandler.format_error_response(
            error_type=ErrorType.NOT_FOUND_ERROR,
            message=message
        )
    
    @staticmethod
    def handle_business_logic_error(error: Exception) -> Dict[str, Any]:
        """
        Handle business logic errors with user-friendly messages.
        
        Args:
            error: The business logic error exception
        
        Returns:
            Dict: Formatted error response
        """
        message = str(error)
        
        # Business logic errors should already have user-friendly messages
        # from the service layer, so we can use them directly
        if not message:
            message = "Unable to complete your request. Please try again."
        
        return ErrorHandler.format_error_response(
            error_type=ErrorType.BUSINESS_LOGIC_ERROR,
            message=message
        )
    
    @staticmethod
    def handle_database_error(error: Exception) -> Dict[str, Any]:
        """
        Handle database errors without exposing internal details.
        
        Args:
            error: The database error exception
        
        Returns:
            Dict: Formatted error response
        """
        # Log the actual error for debugging
        logger.error(f"Database error: {error}", exc_info=True)
        
        # Return generic user-friendly message
        message = (
            "We're having trouble accessing the database right now. "
            "Please try again in a moment."
        )
        
        return ErrorHandler.format_error_response(
            error_type=ErrorType.DATABASE_ERROR,
            message=message
        )
    
    @staticmethod
    def handle_llm_error(error: Exception) -> Dict[str, Any]:
        """
        Handle LLM service errors without exposing internal details.
        
        Args:
            error: The LLM error exception
        
        Returns:
            Dict: Formatted error response
        """
        # Log the actual error for debugging
        logger.error(f"LLM service error: {error}", exc_info=True)
        
        # Return generic user-friendly message
        message = (
            "I'm having trouble understanding your message right now. "
            "Could you please try rephrasing it or be more specific?"
        )
        
        return ErrorHandler.format_error_response(
            error_type=ErrorType.LLM_ERROR,
            message=message
        )
    
    @staticmethod
    def handle_telegram_error(error: Exception) -> Dict[str, Any]:
        """
        Handle Telegram API errors without exposing internal details.
        
        Args:
            error: The Telegram error exception
        
        Returns:
            Dict: Formatted error response
        """
        # Log the actual error for debugging
        logger.error(f"Telegram API error: {error}", exc_info=True)
        
        # Return generic user-friendly message
        message = (
            "We're having trouble sending your message. "
            "Please try again in a moment."
        )
        
        return ErrorHandler.format_error_response(
            error_type=ErrorType.TELEGRAM_ERROR,
            message=message
        )
    
    @staticmethod
    def handle_system_error(error: Exception) -> Dict[str, Any]:
        """
        Handle unexpected system errors without exposing internal details.
        
        Args:
            error: The system error exception
        
        Returns:
            Dict: Formatted error response
        """
        # Log the actual error for debugging
        logger.error(f"System error: {error}", exc_info=True)
        
        # Return generic user-friendly message
        message = (
            "Something unexpected happened. "
            "Please try again, and if the problem persists, contact support."
        )
        
        return ErrorHandler.format_error_response(
            error_type=ErrorType.SYSTEM_ERROR,
            message=message
        )
    
    @staticmethod
    def handle_exception(error: Exception) -> Dict[str, Any]:
        """
        Handle any exception and route to appropriate handler.
        
        Args:
            error: The exception to handle
        
        Returns:
            Dict: Formatted error response
        """
        error_message = str(error).lower()
        
        # Route to specific handler based on error message content
        if "not found" in error_message or "does not exist" in error_message:
            return ErrorHandler.handle_not_found_error(error)
        elif any(keyword in error_message for keyword in ["invalid", "must be", "required", "cannot be"]):
            return ErrorHandler.handle_validation_error(error)
        elif "database" in error_message or "sql" in error_message:
            return ErrorHandler.handle_database_error(error)
        elif "llm" in error_message or "intent" in error_message:
            return ErrorHandler.handle_llm_error(error)
        elif "telegram" in error_message:
            return ErrorHandler.handle_telegram_error(error)
        else:
            # Default to business logic error if it has a user-friendly message
            if error_message and len(error_message) > 10:
                return ErrorHandler.handle_business_logic_error(error)
            else:
                return ErrorHandler.handle_system_error(error)


def format_error_for_telegram(error_response: Dict[str, Any]) -> str:
    """
    Format an error response for display in Telegram.
    
    Args:
        error_response: Error response dictionary from ErrorHandler
    
    Returns:
        str: Formatted error message for Telegram (plain text, no markdown)
    """
    message = error_response.get("message", "An error occurred")
    
    # Return plain text without markdown to avoid parsing issues
    return f"❌ {message}"
