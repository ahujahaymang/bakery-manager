"""
Outbound notification triggers (Requirement 17).

This module adds the *outbound* half of the Telegram/WhatsApp demotion: it pushes
alerts and summaries to the Owner over the tenant's configured
``messaging_platform`` (Req 17.6). It reuses the existing Telegram/WhatsApp send
paths rather than reimplementing platform transport — the running listeners'
async ``(destination, message)`` callables are injected as ``senders`` keyed by
platform, mirroring how ``app/auth/otp_sender.py`` reuses those same paths.

The existing inbound agent command path is intentionally left untouched
(Req 17.5, 17.8): nothing here reads incoming messages or routes commands.

Triggers implemented
--------------------
- Order alert (Req 17.1, 17.3): order id, customer name, ordered items, total.
- Instagram-order alert (Req 17.3): customer handle + detected order details.
- Subscription expiry warning (Req 17.2): expiry date + days remaining, sent at
  most once per calendar day while inside the 7-day pre-expiry window.
- Daily summary (Req 17.4): sales count, sales amount, and pending-order count
  for the preceding 24 hours, sent at most once per calendar day per tenant.

Delivery guarantees
-------------------
Every send is attempted up to 3 times; if all attempts fail the failure is
recorded (Req 17.7). The day-gated senders (expiry warning, daily summary) mark
a day as sent only on *successful* delivery, so a same-day retry after a total
failure is still possible without ever double-delivering.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal
from enum import Enum
from typing import (
    TYPE_CHECKING,
    Awaitable,
    Callable,
    Iterable,
    List,
    Mapping,
    Optional,
    Protocol,
)
from uuid import UUID

if TYPE_CHECKING:  # typing only — avoids any import-time ORM dependency
    from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


# The existing Telegram/WhatsApp send paths share this shape: an async callable
# taking (destination, message). We reuse those callables rather than
# reimplementing platform transport here.
SendFn = Callable[[str, str], Awaitable[None]]

# How many delivery attempts before a notification is recorded as failed
# (Req 17.7: "retry delivery up to 3 times").
DEFAULT_MAX_ATTEMPTS = 3

# The subscription expiry warning window: the 7 calendar days immediately
# preceding the expiry date (Req 17.2).
EXPIRY_WARNING_WINDOW_DAYS = 7


# ── Result / failure types ────────────────────────────────────────────────────

class NotificationKind(str, Enum):
    """The category of an outbound notification."""
    ORDER_ALERT = "order_alert"
    INSTAGRAM_ORDER_ALERT = "instagram_order_alert"
    EXPIRY_WARNING = "expiry_warning"
    DAILY_SUMMARY = "daily_summary"


@dataclass
class NotificationResult:
    """
    Outcome of a delivery attempt sequence.

    ``delivered`` is the single source of truth for whether the notification
    reached the platform. ``attempts`` is how many send attempts were made
    (1..max_attempts). ``error`` holds the last failure message when undelivered.
    """
    kind: NotificationKind
    delivered: bool
    attempts: int
    channel: Optional[str] = None
    error: Optional[str] = None


@dataclass
class NotificationFailure:
    """A recorded delivery failure after all retries were exhausted (Req 17.7)."""
    kind: NotificationKind
    tenant_id: Optional[UUID]
    channel: Optional[str]
    attempts: int
    error: Optional[str]
    at: datetime


FailureRecorder = Callable[[NotificationFailure], None]


# ── Once-per-calendar-day ledger ──────────────────────────────────────────────

class SentLedger(Protocol):
    """
    Tracks which (tenant, kind, calendar-day) notifications have already been
    delivered, so day-gated senders fire at most once per calendar day
    (Req 17.2, 17.4). Kept as a Protocol so a durable implementation (DB-backed)
    can be substituted in production without changing the Notifier.
    """

    def was_sent(self, tenant_id: UUID, kind: NotificationKind, day: date) -> bool: ...

    def mark_sent(self, tenant_id: UUID, kind: NotificationKind, day: date) -> None: ...


class InMemorySentLedger:
    """
    Default in-process :class:`SentLedger`.

    Suitable for a single long-lived process (the polling bot / scheduler). For
    multi-process deployments, replace with a DB-backed ledger keyed on
    (tenant_id, kind, day).
    """

    def __init__(self) -> None:
        self._sent: set[tuple[str, str, str]] = set()

    def _key(self, tenant_id: UUID, kind: NotificationKind, day: date) -> tuple[str, str, str]:
        return (str(tenant_id), kind.value, day.isoformat())

    def was_sent(self, tenant_id: UUID, kind: NotificationKind, day: date) -> bool:
        return self._key(tenant_id, kind, day) in self._sent

    def mark_sent(self, tenant_id: UUID, kind: NotificationKind, day: date) -> None:
        self._sent.add(self._key(tenant_id, kind, day))


# ── Daily-summary figures ──────────────────────────────────────────────────────

@dataclass
class DailySummaryData:
    """
    The figures reported in a daily summary (Req 17.4), computed for the
    preceding 24-hour period.
    """
    sales_count: int
    sales_amount: Decimal
    pending_order_count: int
    period_start: datetime
    period_end: datetime


def compute_daily_summary(
    db: "Session",
    tenant_id: UUID,
    *,
    now: Optional[datetime] = None,
) -> DailySummaryData:
    """
    Compute the daily-summary figures from a tenant's business DB for the
    preceding 24-hour period (Req 17.4).

    - sales_count / sales_amount: completed payments recorded in the window.
    - pending_order_count: orders created in the window still in "pending".

    Uses the same tenant-scoped query style as :mod:`app.services.reporting_service`.
    """
    from app.models import Order, Payment  # lazy import — keep module cheap to import

    now = now or datetime.utcnow()
    period_start = now - timedelta(hours=24)

    payments = (
        db.query(Payment)
        .filter(
            Payment.tenant_id == tenant_id,
            Payment.status == "completed",
            Payment.created_at >= period_start,
            Payment.created_at <= now,
        )
        .all()
    )
    sales_count = len(payments)
    sales_amount = sum((p.amount for p in payments), Decimal("0"))

    pending_order_count = (
        db.query(Order)
        .filter(
            Order.tenant_id == tenant_id,
            Order.status == "pending",
            Order.created_at >= period_start,
            Order.created_at <= now,
        )
        .count()
    )

    return DailySummaryData(
        sales_count=sales_count,
        sales_amount=Decimal(sales_amount),
        pending_order_count=pending_order_count,
        period_start=period_start,
        period_end=now,
    )


# ── Expiry-window helper ───────────────────────────────────────────────────────

def expiry_warning_due(tenant, today: Optional[date] = None) -> Optional[int]:
    """
    Return the number of days remaining until expiry when ``tenant`` is inside
    the 7-day pre-expiry warning window, or ``None`` when no warning is due
    (Req 17.2).

    The window is the 7 calendar days immediately preceding the expiry date, so
    it fires for ``days_remaining`` in 1..7 inclusive. Tenants without an expiry
    date, or already expired, are not in the window.
    """
    expires_at = getattr(tenant, "subscription_expires_at", None)
    if expires_at is None:
        return None
    today = today or date.today()
    expiry_date = expires_at.date() if isinstance(expires_at, datetime) else expires_at
    days_remaining = (expiry_date - today).days
    if 1 <= days_remaining <= EXPIRY_WARNING_WINDOW_DAYS:
        return days_remaining
    return None


# ── Message formatting (each contains the required fields) ─────────────────────

def _format_items(items: Iterable[Mapping]) -> str:
    """Render ordered items as readable lines. Tolerant of partial data."""
    lines: List[str] = []
    for item in items or []:
        name = item.get("name") or item.get("recipe_name") or "item"
        qty = item.get("quantity")
        price = item.get("price")
        if price is None:
            price = item.get("selling_price")
        parts = [str(name)]
        if qty is not None:
            parts.append(f"x{qty}")
        if price is not None:
            parts.append(f"@ {price}")
        lines.append("• " + " ".join(parts))
    return "\n".join(lines) if lines else "• (no items)"


def format_order_alert(
    order_id,
    customer_name: Optional[str],
    items: Iterable[Mapping],
    total,
) -> str:
    """Order alert message — order id, customer, items, total (Req 17.1, 17.3)."""
    return (
        "🧾 New order\n"
        f"Order: {order_id}\n"
        f"Customer: {customer_name or 'Unknown'}\n"
        f"Items:\n{_format_items(items)}\n"
        f"Total: {total}"
    )


def format_instagram_order_alert(
    customer_handle: str,
    items: Iterable[Mapping],
    *,
    customer_name: Optional[str] = None,
    delivery_date: Optional[str] = None,
    delivery_address: Optional[str] = None,
) -> str:
    """Instagram-order detection alert — customer handle + details (Req 17.3)."""
    lines = [
        "📸 Instagram order detected",
        f"From: @{customer_handle.lstrip('@')}",
    ]
    if customer_name:
        lines.append(f"Customer: {customer_name}")
    lines.append(f"Items:\n{_format_items(items)}")
    if delivery_date:
        lines.append(f"Delivery date: {delivery_date}")
    if delivery_address:
        lines.append(f"Delivery to: {delivery_address}")
    return "\n".join(lines)


def format_expiry_warning(expiry_date, days_remaining: int) -> str:
    """Subscription expiry warning — expiry date + days remaining (Req 17.2)."""
    disp = expiry_date.date() if isinstance(expiry_date, datetime) else expiry_date
    day_word = "day" if days_remaining == 1 else "days"
    return (
        "⏳ Subscription expiring soon\n"
        f"Expires on: {disp}\n"
        f"Days remaining: {days_remaining} {day_word}\n"
        "Renew to keep uninterrupted access."
    )


def format_daily_summary(summary: DailySummaryData) -> str:
    """Daily summary — sales count, sales amount, pending orders (Req 17.4)."""
    return (
        "📊 Daily summary (last 24h)\n"
        f"Sales: {summary.sales_count}\n"
        f"Sales amount: {summary.sales_amount}\n"
        f"Pending orders: {summary.pending_order_count}"
    )


# ── Notifier ───────────────────────────────────────────────────────────────────

class Notifier:
    """
    Sends outbound notifications over a tenant's configured ``messaging_platform``
    (Req 17.6), with bounded delivery retry and failure recording (Req 17.7).

    ``senders`` maps a platform name ("telegram" | "whatsapp") to the existing
    async send callable for that platform. The Owner destination is the tenant's
    ``chat_id`` (the same value the existing listeners already send to).
    """

    def __init__(
        self,
        senders: Mapping[str, SendFn],
        *,
        max_attempts: int = DEFAULT_MAX_ATTEMPTS,
        ledger: Optional[SentLedger] = None,
        failure_recorder: Optional[FailureRecorder] = None,
        clock: Optional[Callable[[], datetime]] = None,
    ) -> None:
        self._senders = {str(k).lower(): v for k, v in senders.items()}
        self._max_attempts = max(1, int(max_attempts))
        self._ledger: SentLedger = ledger or InMemorySentLedger()
        self._failure_recorder = failure_recorder
        self._clock = clock or datetime.utcnow

    # ── public senders ─────────────────────────────────────────────────────

    async def send_order_alert(
        self,
        tenant,
        order_id,
        customer_name: Optional[str],
        items: Iterable[Mapping],
        total,
    ) -> NotificationResult:
        """Send a new-order alert to the Owner (Req 17.1, 17.3)."""
        message = format_order_alert(order_id, customer_name, items, total)
        return await self._deliver(tenant, NotificationKind.ORDER_ALERT, message)

    async def send_instagram_order_alert(
        self,
        tenant,
        customer_handle: str,
        items: Iterable[Mapping],
        *,
        customer_name: Optional[str] = None,
        delivery_date: Optional[str] = None,
        delivery_address: Optional[str] = None,
    ) -> NotificationResult:
        """Send an Instagram-order detection alert to the Owner (Req 17.3)."""
        message = format_instagram_order_alert(
            customer_handle,
            items,
            customer_name=customer_name,
            delivery_date=delivery_date,
            delivery_address=delivery_address,
        )
        return await self._deliver(
            tenant, NotificationKind.INSTAGRAM_ORDER_ALERT, message
        )

    async def send_expiry_warning(
        self,
        tenant,
        *,
        today: Optional[date] = None,
    ) -> Optional[NotificationResult]:
        """
        Send a subscription expiry warning if the tenant is inside the 7-day
        window and no warning has been delivered yet today (Req 17.2).

        Returns ``None`` when no warning is due or one was already sent today.
        """
        today = today or self._clock().date()
        days_remaining = expiry_warning_due(tenant, today)
        if days_remaining is None:
            return None

        tenant_id = getattr(tenant, "tenant_id", None)
        if tenant_id is not None and self._ledger.was_sent(
            tenant_id, NotificationKind.EXPIRY_WARNING, today
        ):
            return None

        message = format_expiry_warning(
            getattr(tenant, "subscription_expires_at"), days_remaining
        )
        result = await self._deliver(tenant, NotificationKind.EXPIRY_WARNING, message)
        if result.delivered and tenant_id is not None:
            self._ledger.mark_sent(tenant_id, NotificationKind.EXPIRY_WARNING, today)
        return result

    async def send_daily_summary(
        self,
        tenant,
        summary: DailySummaryData,
        *,
        today: Optional[date] = None,
    ) -> Optional[NotificationResult]:
        """
        Send the daily summary if it has not already been delivered today
        (Req 17.4). Figures are supplied by :func:`compute_daily_summary`.

        Returns ``None`` when a summary was already sent today.
        """
        today = today or self._clock().date()
        tenant_id = getattr(tenant, "tenant_id", None)
        if tenant_id is not None and self._ledger.was_sent(
            tenant_id, NotificationKind.DAILY_SUMMARY, today
        ):
            return None

        message = format_daily_summary(summary)
        result = await self._deliver(tenant, NotificationKind.DAILY_SUMMARY, message)
        if result.delivered and tenant_id is not None:
            self._ledger.mark_sent(tenant_id, NotificationKind.DAILY_SUMMARY, today)
        return result

    # ── delivery with bounded retry (Req 17.7) ──────────────────────────────

    async def _deliver(
        self,
        tenant,
        kind: NotificationKind,
        message: str,
    ) -> NotificationResult:
        """
        Deliver ``message`` to the tenant's Owner over the configured platform,
        retrying up to ``max_attempts`` times and recording a failure if all
        attempts are exhausted (Req 17.6, 17.7).
        """
        platform = (getattr(tenant, "messaging_platform", None) or "telegram").lower()
        destination = getattr(tenant, "chat_id", None)
        tenant_id = getattr(tenant, "tenant_id", None)
        send_fn = self._senders.get(platform)

        # Misconfiguration (no send path for platform, or no destination) is a
        # delivery failure with no attempts possible — record it (Req 17.7).
        if send_fn is None or not destination:
            reason = (
                f"no send path configured for platform '{platform}'"
                if send_fn is None
                else "tenant has no destination chat_id"
            )
            return self._fail(kind, tenant_id, platform, attempts=0, error=reason)

        last_error: Optional[str] = None
        for attempt in range(1, self._max_attempts + 1):
            try:
                await send_fn(destination, message)
                return NotificationResult(
                    kind=kind, delivered=True, attempts=attempt, channel=platform
                )
            except Exception as exc:  # transient transport failure → retry
                last_error = str(exc)
                logger.warning(
                    "Notification %s delivery attempt %d/%d failed on %s: %s",
                    kind.value, attempt, self._max_attempts, platform, exc,
                )

        # All attempts exhausted (Req 17.7).
        return self._fail(
            kind, tenant_id, platform, attempts=self._max_attempts, error=last_error
        )

    def _fail(
        self,
        kind: NotificationKind,
        tenant_id: Optional[UUID],
        channel: Optional[str],
        *,
        attempts: int,
        error: Optional[str],
    ) -> NotificationResult:
        """Record a delivery failure and return the failed result (Req 17.7)."""
        failure = NotificationFailure(
            kind=kind,
            tenant_id=tenant_id,
            channel=channel,
            attempts=attempts,
            error=error,
            at=self._clock(),
        )
        logger.error(
            "[NOTIFY_FAILED] kind=%s tenant=%s channel=%s attempts=%d error=%s",
            kind.value, tenant_id, channel, attempts, error,
        )
        if self._failure_recorder is not None:
            try:
                self._failure_recorder(failure)
            except Exception:  # never let failure recording raise
                logger.exception("failure_recorder raised while recording %s", kind.value)
        return NotificationResult(
            kind=kind, delivered=False, attempts=attempts, channel=None, error=error
        )
