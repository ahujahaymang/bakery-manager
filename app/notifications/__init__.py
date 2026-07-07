"""
Outbound notification triggers for the app-first pivot (Requirement 17).

Telegram/WhatsApp are demoted from the primary workspace to a notification and
async-entry channel: the App becomes the primary creation surface (Req 17.6),
while the platform is used to *push* alerts and summaries to the Owner. The
existing inbound agent command path is untouched (Req 17.5, 17.8) — this package
only adds outbound senders.

Public API is exported from ``app.notifications.notifier``.
"""

from app.notifications.notifier import (
    Notifier,
    NotificationKind,
    NotificationResult,
    NotificationFailure,
    DailySummaryData,
    SentLedger,
    InMemorySentLedger,
    compute_daily_summary,
    expiry_warning_due,
    format_order_alert,
    format_instagram_order_alert,
    format_expiry_warning,
    format_daily_summary,
)

__all__ = [
    "Notifier",
    "NotificationKind",
    "NotificationResult",
    "NotificationFailure",
    "DailySummaryData",
    "SentLedger",
    "InMemorySentLedger",
    "compute_daily_summary",
    "expiry_warning_due",
    "format_order_alert",
    "format_instagram_order_alert",
    "format_expiry_warning",
    "format_daily_summary",
]
