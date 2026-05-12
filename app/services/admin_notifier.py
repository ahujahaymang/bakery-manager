"""
Admin Notifier Service.

Centralises all admin notifications:
- Error reports (from owners or automatic on crash)
- Feature requests (/request command)
- Feedback (/feedback command)
- New user signups (approval flow)
- Subscription events (trial started, expired)

All notifications are sent via the injected send_fn callable so this
service is platform-agnostic — works with Telegram, WhatsApp, or any
future platform.

Notifications are also logged to the Python logger so they appear in
CloudWatch Logs for persistent tracking.
"""

import logging
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Callable, Awaitable, Optional
from uuid import UUID

logger = logging.getLogger(__name__)


class NotificationType(str, Enum):
    ERROR = "error"
    FEATURE_REQUEST = "feature_request"
    FEEDBACK = "feedback"
    NEW_SIGNUP = "new_signup"
    SUBSCRIPTION_EVENT = "subscription_event"


@dataclass
class AdminNotification:
    """Structured notification sent to admin."""
    type: NotificationType
    chat_id: str
    business_name: Optional[str]
    content: str
    timestamp: datetime


class AdminNotifier:
    """
    Sends structured notifications to the admin.

    Injected with a send_fn at startup so it remains platform-agnostic.
    All notifications are also logged for CloudWatch / persistent tracking.
    """

    def __init__(
        self,
        admin_chat_id: str,
        send_fn: Optional[Callable[[str, str], Awaitable[None]]] = None,
    ):
        """
        Args:
            admin_chat_id: The admin's chat ID to send notifications to
            send_fn: Async callable(chat_id, message) — injected by the platform adapter
        """
        self.admin_chat_id = admin_chat_id
        self._send = send_fn

    def set_send_fn(self, send_fn: Callable[[str, str], Awaitable[None]]) -> None:
        """Set the send function after initialisation (e.g. after bot starts)."""
        self._send = send_fn

    # ── Public API ─────────────────────────────────────────────────────────

    async def notify_error(
        self,
        chat_id: str,
        user_message: str,
        error: Exception,
        business_name: Optional[str] = None,
    ) -> None:
        """
        Notify admin when an owner encounters an unhandled error.
        Called automatically by RequestHandler on exceptions.
        """
        content = (
            f"⚠️ *Error in production*\n\n"
            f"*Business:* {business_name or chat_id}\n"
            f"*Message:* `{user_message[:100]}`\n"
            f"*Error:* `{type(error).__name__}: {str(error)[:200]}`"
        )
        await self._notify(NotificationType.ERROR, chat_id, business_name, content)

    async def notify_feature_request(
        self,
        chat_id: str,
        request_text: str,
        business_name: Optional[str] = None,
    ) -> None:
        """
        Notify admin of a feature request from an owner.
        Triggered by /request command.
        """
        content = (
            f"💡 *Feature Request*\n\n"
            f"*Business:* {business_name or chat_id}\n"
            f"*Request:* {request_text[:500]}"
        )
        await self._notify(NotificationType.FEATURE_REQUEST, chat_id, business_name, content)

    async def notify_feedback(
        self,
        chat_id: str,
        feedback_text: str,
        business_name: Optional[str] = None,
    ) -> None:
        """
        Notify admin of feedback from an owner.
        Triggered by /feedback command.
        """
        content = (
            f"📝 *Feedback*\n\n"
            f"*Business:* {business_name or chat_id}\n"
            f"*Feedback:* {feedback_text[:500]}"
        )
        await self._notify(NotificationType.FEEDBACK, chat_id, business_name, content)

    async def notify_new_signup(
        self,
        chat_id: str,
        business_name: str,
        country: str,
    ) -> None:
        """
        Notify admin of a new user signup awaiting approval.
        Triggered after onboarding completes.
        """
        content = (
            f"🆕 *New signup!*\n\n"
            f"*Business:* {business_name}\n"
            f"*Country:* {country}\n"
            f"*Chat ID:* `{chat_id}`\n\n"
            f"To start their 7-day trial:\n"
            f"`/trial {chat_id}`\n\n"
            f"To approve 30-day subscription:\n"
            f"`/approve {chat_id} 30`"
        )
        await self._notify(NotificationType.NEW_SIGNUP, chat_id, business_name, content)

    async def send_to_user(self, chat_id: str, message: str) -> None:
        """
        Send a message directly to any user (e.g. owner notification on trial start).
        Unlike _notify(), this sends to the given chat_id, not the admin.
        """
        if not self._send:
            logger.warning(f"send_to_user called but no send_fn set — message to {chat_id} dropped")
            return
        try:
            await self._send(chat_id, message)
        except Exception as e:
            logger.error(f"Failed to send message to user {chat_id}: {e}")

    async def notify_subscription_event(
        self,
        chat_id: str,
        business_name: str,
        event: str,
        days: Optional[int] = None,
    ) -> None:
        """
        Notify admin of a subscription event (trial started, expired, etc.).
        """
        days_str = f" ({days} days)" if days else ""
        content = (
            f"💳 *Subscription event*\n\n"
            f"*Business:* {business_name}\n"
            f"*Event:* {event}{days_str}\n"
            f"*Chat ID:* `{chat_id}`"
        )
        await self._notify(NotificationType.SUBSCRIPTION_EVENT, chat_id, business_name, content)

    # ── Internal ───────────────────────────────────────────────────────────

    async def _notify(
        self,
        notification_type: NotificationType,
        chat_id: str,
        business_name: Optional[str],
        content: str,
    ) -> None:
        """Send notification to admin and log it."""
        notification = AdminNotification(
            type=notification_type,
            chat_id=chat_id,
            business_name=business_name,
            content=content,
            timestamp=datetime.utcnow(),
        )

        # Always log — appears in CloudWatch Logs
        logger.info(
            f"[ADMIN_NOTIFY] type={notification_type.value} "
            f"chat={chat_id} business={business_name or 'unknown'} "
            f"content={content[:100]}"
        )

        # Send via platform if available
        if not self._send or not self.admin_chat_id:
            return

        try:
            await self._send(self.admin_chat_id, content)
        except Exception as e:
            logger.error(f"Failed to send admin notification: {e}")
