"""
Error Handler — single entry point for all errors shown to owners.

Every error that reaches an owner goes through ErrorHandler.handle().
It does three things in one call:
  1. Classifies the error and picks a user-friendly message
  2. Logs it (appears in CloudWatch)
  3. Notifies the admin via AdminNotifier

Usage:
    # In any handler or service:
    msg = await ErrorHandler.handle(error, chat_id, context="show recipes")
    return msg   # send this to the owner

The AdminNotifier is injected once at startup via ErrorHandler.set_notifier().
"""

import logging
from enum import Enum
from typing import Optional

logger = logging.getLogger(__name__)


class ErrorType(str, Enum):
    VALIDATION    = "validation"
    NOT_FOUND     = "not_found"
    BUSINESS      = "business_logic"
    DATABASE      = "database"
    LLM           = "llm"
    IMAGE         = "image_processing"
    SYSTEM        = "system"


# User-facing messages per error type
_USER_MESSAGES = {
    ErrorType.VALIDATION: "The information you provided isn't valid. Please check and try again.",
    ErrorType.NOT_FOUND:  "I couldn't find what you're looking for. Please check the details.",
    ErrorType.BUSINESS:   None,   # use the exception message directly — it's already user-friendly
    ErrorType.DATABASE:   "I'm having trouble accessing your data right now. Please try again.",
    ErrorType.LLM:        "I had trouble understanding that. Please try rephrasing.",
    ErrorType.IMAGE:      "I couldn't process the image. Please try again with a clearer photo.",
    ErrorType.SYSTEM:     "Something unexpected happened. Please try again.",
}


class ErrorHandler:
    """
    Single entry point for all owner-facing errors.

    Inject the AdminNotifier once at startup:
        ErrorHandler.set_notifier(admin_notifier)

    Then call from anywhere:
        msg = await ErrorHandler.handle(error, chat_id, context="...")
    """

    _notifier = None  # AdminNotifier instance, set at startup

    @classmethod
    def set_notifier(cls, notifier) -> None:
        """Inject the AdminNotifier. Called once when the bot starts."""
        cls._notifier = notifier

    @classmethod
    async def handle(
        cls,
        error: Exception,
        chat_id: str,
        context: str = "",
        business_name: Optional[str] = None,
        notify_admin: bool = True,
    ) -> str:
        """
        Handle an error end-to-end:
          1. Classify it
          2. Build a user-facing message
          3. Log it
          4. Notify admin (unless notify_admin=False)

        Args:
            error:         The exception that occurred
            chat_id:       Owner's chat ID (for admin notification)
            context:       What the owner was doing (e.g. "show recipes", "[catalog image]")
            business_name: Owner's business name if known
            notify_admin:  Set False to suppress admin notification (e.g. for expected errors)

        Returns:
            User-facing string to send to the owner.
        """
        error_type = cls._classify(error)
        user_msg = cls._user_message(error_type, error)

        # Log with full detail for CloudWatch
        logger.error(
            f"[ERROR] type={error_type.value} chat={chat_id} "
            f"context={context!r} error={type(error).__name__}: {error}",
            exc_info=True,
        )

        # Notify admin
        if notify_admin and cls._notifier:
            try:
                await cls._notifier.notify_error(
                    chat_id=chat_id,
                    user_message=context or "unknown",
                    error=error,
                    business_name=business_name,
                )
            except Exception as notify_err:
                logger.error(f"Failed to notify admin of error: {notify_err}")

        return user_msg

    @classmethod
    def handle_sync(
        cls,
        error: Exception,
        context: str = "",
    ) -> str:
        """
        Synchronous version — logs only, no admin notification.
        Use when you can't await (e.g. in a sync context).
        """
        error_type = cls._classify(error)
        logger.error(
            f"[ERROR] type={error_type.value} context={context!r} "
            f"error={type(error).__name__}: {error}",
            exc_info=True,
        )
        return cls._user_message(error_type, error)

    # ── Internal ───────────────────────────────────────────────────────────

    @classmethod
    def _classify(cls, error: Exception) -> ErrorType:
        msg = str(error).lower()
        name = type(error).__name__.lower()

        if "not found" in msg or "does not exist" in msg:
            return ErrorType.NOT_FOUND
        if any(k in msg for k in ("invalid", "must be", "required", "cannot be", "positive")):
            return ErrorType.VALIDATION
        if "database" in msg or "sql" in msg or "operational" in name:
            return ErrorType.DATABASE
        if "image" in msg or "vision" in msg or "parse image" in msg or "catalog" in msg:
            return ErrorType.IMAGE
        if any(k in msg for k in ("llm", "openai", "bedrock", "model", "token", "api")):
            return ErrorType.LLM
        if isinstance(error, ValueError) and len(msg) > 10:
            return ErrorType.BUSINESS
        return ErrorType.SYSTEM

    @classmethod
    def _user_message(cls, error_type: ErrorType, error: Exception) -> str:
        template = _USER_MESSAGES.get(error_type)
        if template is None:
            # BUSINESS errors: use the exception message directly
            msg = str(error)
            if msg:
                return f"❌ {msg}"
            return "❌ Unable to complete your request. Please try again."
        return f"❌ {template}\n\nIf this keeps happening, use */report* to let us know."


# ── Backwards-compat shim ──────────────────────────────────────────────────
# Some older call sites use format_error_for_telegram(ErrorHandler.handle_exception(e)).
# Keep this working without changes to those sites.

def format_error_for_telegram(error_response) -> str:
    """Backwards-compat shim. Prefer ErrorHandler.handle() for new code."""
    if isinstance(error_response, str):
        return error_response
    if isinstance(error_response, dict):
        return f"❌ {error_response.get('message', 'An error occurred')}"
    return "❌ An error occurred"
