"""
Tiered OTP delivery for the app-first pivot Auth_Service.

Requirement 3 asks OTP delivery to prefer a low-cost, already-trusted channel
for known users (their Telegram/WhatsApp Notification_Channel) and only fall
back to paid SMS when no channel exists or the channel does not confirm
delivery. This module implements that tiering behind a single ``OtpSender``
interface so ``AuthService`` (task 3.2) delegates delivery without knowing the
mechanics.

Layout
------
- ``OtpSender``            — the delivery interface (async ``send``).
- ``DeliveryResult``       — outcome: whether the OTP was confirmed delivered,
                             which channel carried it, and a per-tier attempt log.
- ``NotificationChannelSender`` — sends over the *existing* Telegram/WhatsApp
                             send paths (reused, not reimplemented). It resolves
                             whether a phone is associated with a channel via an
                             injectable ``ChannelResolver``.
- ``SmsSender``            — sends via a swappable ``SmsProvider`` selected by
                             config (Indian DLT gateway in production).
- ``TieredOtpSender``      — orchestrates channel-first → SMS-fallback and
                             enforces the confirmation windows of Requirement 3.

All senders conform to ``OtpSender`` and are constructor-injected, so they can
be mocked wholesale in tests (see property test 3.11).

Requirement mapping
-------------------
- 3.1 phone associated with a channel → send over the channel.
- 3.2 no channel → send by SMS.
- 3.3 no channel delivery confirmation within the window → record the channel
      attempt as failed and send by SMS.
- 3.4 no SMS confirmation within the window → return a delivery-failure result
      and do NOT mark the OTP delivered.
- 2.4 delivery failure is reported to the caller so it can offer a retry.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Awaitable, Callable, List, Optional, Protocol

from app.config import settings

logger = logging.getLogger(__name__)


# ── Delivery result types ────────────────────────────────────────────────────

class DeliveryChannel(str, Enum):
    """The transport an OTP was (or would be) delivered over."""
    TELEGRAM = "telegram"
    WHATSAPP = "whatsapp"
    SMS = "sms"


@dataclass
class DeliveryAttempt:
    """
    A single delivery attempt over one channel.

    ``confirmed`` is True only when the send completed within its confirmation
    window without error. Failed and unconfirmed attempts are retained so the
    caller can record which channels were tried (Requirement 3.3).
    """
    channel: DeliveryChannel
    confirmed: bool
    error: Optional[str] = None


@dataclass
class DeliveryResult:
    """
    The outcome of an OTP delivery request.

    ``delivered`` is the single source of truth the caller uses to decide
    whether to mark the OTP as delivered (Requirement 3.4): it is True only if
    some tier confirmed delivery. ``channel`` names the tier that succeeded (or
    None on total failure). ``attempts`` is the ordered log of every tier tried.
    """
    delivered: bool
    channel: Optional[DeliveryChannel] = None
    attempts: List[DeliveryAttempt] = field(default_factory=list)

    @property
    def success(self) -> bool:
        """
        Alias for :attr:`delivered`.

        ``AuthService`` (task 3.2) reads the delivery outcome via a ``success``
        attribute, so exposing this alias keeps the two components interoperable
        without either side depending on the other's field naming.
        """
        return self.delivered


# ── Delivery interface ───────────────────────────────────────────────────────

class OtpSender(Protocol):
    """Anything that can attempt to deliver an OTP ``code`` to ``phone``."""

    async def send(self, phone: str, code: str) -> DeliveryResult: ...


# The existing Telegram/WhatsApp send paths share this shape:
# an async callable taking (destination, message). We reuse those callables
# rather than reimplementing platform transport here.
SendFn = Callable[[str, str], Awaitable[None]]


# ── OTP message text ─────────────────────────────────────────────────────────

def format_otp_message(code: str) -> str:
    """
    Render the user-facing OTP message.

    In production the SMS variant of this text must match the DLT-registered
    template exactly (Requirement 3.2 operational dependency); keep this wording
    and the registered template in sync.
    """
    return (
        f"Your KitchenOS verification code is {code}. "
        f"It expires in 5 minutes. Do not share it with anyone."
    )


# ── Notification-channel resolution (phone → existing channel) ────────────────

@dataclass
class ChannelTarget:
    """
    Where a phone's Notification_Channel messages should go.

    ``platform`` is "telegram" or "whatsapp"; ``destination`` is the address the
    matching existing send path expects — the Telegram ``chat_id`` for Telegram,
    or the E.164 phone number without ``+`` for WhatsApp.
    """
    platform: str
    destination: str


class ChannelResolver(Protocol):
    """
    Resolves whether a phone number has an associated Notification_Channel.

    Returns a :class:`ChannelTarget` when the phone maps to a known
    Telegram/WhatsApp channel, or ``None`` when it does not (SMS-first case,
    Requirement 3.2). Kept as a Protocol so tests inject a trivial resolver.
    """

    def resolve(self, phone: str) -> Optional[ChannelTarget]: ...


def normalize_phone(phone: str) -> str:
    """Normalize a phone number to bare digits (drops ``+``, spaces, dashes)."""
    if not phone:
        return ""
    return "".join(ch for ch in phone if ch.isdigit())


class RegistryChannelResolver:
    """
    Resolve phone → Notification_Channel from the registry DB.

    A phone has a channel when a registry ``User`` with that phone belongs to a
    ``Tenant`` that already talks to us over Telegram/WhatsApp. The tenant's
    ``messaging_platform`` and ``chat_id`` (which is the phone number itself for
    WhatsApp tenants) give the concrete destination — mirroring how
    ``tenant_service`` maps chat_id → tenant elsewhere in the codebase.

    Any lookup error resolves to "no channel" so a registry hiccup degrades to
    the SMS tier rather than blocking delivery.
    """

    def __init__(self, registry_db_factory: Callable[[], object]):
        """
        Args:
            registry_db_factory: zero-arg callable returning a registry DB
                Session (e.g. ``lambda: next(get_registry_db())``). Owned and
                closed by this resolver per call.
        """
        self._registry_db_factory = registry_db_factory

    def resolve(self, phone: str) -> Optional[ChannelTarget]:
        normalized = normalize_phone(phone)
        if not normalized:
            return None

        # Imported lazily so this module has no import-time dependency on the
        # ORM models (keeps it cheap to import and easy to mock).
        from app.auth.models_auth import User
        from app.services.tenant_service import TenantService

        db = None
        try:
            db = self._registry_db_factory()
            # ``User.phone`` is stored preserving a leading ``+`` (AuthService /
            # onboarding), but ``normalize_phone`` strips it to bare digits.
            # Match both forms (plus the raw input) so a stored ``+91…`` number
            # still resolves to its Telegram/WhatsApp channel instead of silently
            # falling back to SMS.
            candidates = [normalized, f"+{normalized}"]
            raw = (phone or "").strip()
            if raw and raw not in candidates:
                candidates.append(raw)
            user = (
                db.query(User)
                .filter(User.phone.in_(candidates))
                .order_by(User.created_at.asc())
                .first()
            )
            if user is None:
                return None

            tenant = TenantService(db).get_tenant_by_id(user.tenant_id)
            if tenant is None or not tenant.chat_id:
                return None

            platform = (tenant.messaging_platform or "telegram").lower()
            if platform not in ("telegram", "whatsapp"):
                return None
            return ChannelTarget(platform=platform, destination=tenant.chat_id)
        except Exception as exc:  # never let resolution break delivery
            logger.warning("Channel resolution failed for phone: %s", exc)
            return None
        finally:
            if db is not None:
                try:
                    db.close()
                except Exception:
                    pass


# ── Notification-channel sender (reuses existing Telegram/WhatsApp paths) ─────

class NotificationChannelSender:
    """
    Deliver an OTP over a phone's existing Telegram/WhatsApp Notification_Channel.

    This class does not reimplement platform transport: it reuses the same async
    send callables the listeners already expose —
    ``TelegramBotListener._send_to_chat`` / ``AdminNotifier.send_to_user`` for
    Telegram and ``WhatsAppListener.send`` for WhatsApp — both of which take
    ``(destination, message)``. Wire the running listeners' methods in as
    ``telegram_send`` / ``whatsapp_send`` at startup.

    A send is considered *confirmed* when the underlying send path returns
    without raising; if it raises (or is cancelled by the caller's timeout), the
    attempt is unconfirmed. The existing paths raise on transport failure and
    return on success, so this is the natural confirmation signal available.
    """

    def __init__(
        self,
        resolver: ChannelResolver,
        telegram_send: Optional[SendFn] = None,
        whatsapp_send: Optional[SendFn] = None,
    ):
        self._resolver = resolver
        self._telegram_send = telegram_send
        self._whatsapp_send = whatsapp_send

    def channel_for(self, phone: str) -> Optional[DeliveryChannel]:
        """
        Report which channel (if any) an OTP to ``phone`` would use.

        Returns ``None`` when the phone has no associated channel, or the
        channel exists but no send path was wired for its platform — either way
        the caller should treat it as "no usable channel" and go straight to SMS
        (Requirement 3.2).
        """
        target = self._resolver.resolve(phone)
        if target is None:
            return None
        if target.platform == "telegram" and self._telegram_send is not None:
            return DeliveryChannel.TELEGRAM
        if target.platform == "whatsapp" and self._whatsapp_send is not None:
            return DeliveryChannel.WHATSAPP
        return None

    def has_channel(self, phone: str) -> bool:
        """True when ``phone`` has a usable Notification_Channel (Req 3.1)."""
        return self.channel_for(phone) is not None

    async def send(self, phone: str, code: str) -> DeliveryResult:
        """
        Attempt delivery over the resolved channel.

        Returns ``delivered=True`` with the carrying channel on success; returns
        ``delivered=False`` (with a recorded failed attempt) when there is no
        usable channel or the send path raises.
        """
        target = self._resolver.resolve(phone)
        if target is None:
            return DeliveryResult(delivered=False, channel=None, attempts=[])

        if target.platform == "telegram":
            channel, send_fn = DeliveryChannel.TELEGRAM, self._telegram_send
        elif target.platform == "whatsapp":
            channel, send_fn = DeliveryChannel.WHATSAPP, self._whatsapp_send
        else:
            return DeliveryResult(delivered=False, channel=None, attempts=[])

        if send_fn is None:
            attempt = DeliveryAttempt(
                channel=channel,
                confirmed=False,
                error="no send path configured for platform",
            )
            return DeliveryResult(delivered=False, channel=None, attempts=[attempt])

        message = format_otp_message(code)
        try:
            await send_fn(target.destination, message)
        except Exception as exc:
            logger.warning("Channel OTP send failed on %s: %s", channel.value, exc)
            return DeliveryResult(
                delivered=False,
                channel=None,
                attempts=[DeliveryAttempt(channel=channel, confirmed=False, error=str(exc))],
            )
        return DeliveryResult(
            delivered=True,
            channel=channel,
            attempts=[DeliveryAttempt(channel=channel, confirmed=True)],
        )


# ── SMS provider abstraction (swappable via config) ───────────────────────────

class SmsProvider(Protocol):
    """
    A concrete SMS gateway. ``SmsSender`` codes against this abstraction so the
    provider can be swapped via config without touching delivery logic.
    """

    async def send_sms(self, phone: str, message: str) -> None:
        """Send ``message`` to ``phone``; raise on any delivery failure."""
        ...


class LoggingSmsProvider:
    """
    Dev-only provider that logs the OTP instead of sending it, and reports
    success. Selected with ``SMS_PROVIDER=stub``. NEVER enable in production —
    it exposes the code in logs and does not actually deliver anything.
    """

    async def send_sms(self, phone: str, message: str) -> None:
        logger.info("[SMS STUB] to=%s message=%s", normalize_phone(phone), message)


class HttpDltSmsProvider:
    """
    Generic HTTP SMS gateway provider for an Indian DLT-registered sender.
    Selected with ``SMS_PROVIDER=http``.

    OPERATIONAL DEPENDENCY: real delivery requires the sender id
    (``SMS_SENDER_ID``) and OTP message template (``SMS_DLT_TEMPLATE_ID``) to be
    pre-registered and approved under DLT with a telecom operator. This is an
    operational task, not a code dependency; the values are supplied via config.

    The exact request shape differs per gateway. This implementation posts a
    common form payload and treats a non-2xx response as a delivery failure. To
    adopt a different gateway, either subclass this or add another provider and
    select it via ``SMS_PROVIDER`` — no change to the delivery tiers is needed.
    """

    def __init__(
        self,
        base_url: str,
        api_key: str,
        sender_id: str,
        template_id: str,
    ):
        if not base_url:
            raise ValueError("HttpDltSmsProvider requires SMS_API_BASE_URL")
        self._base_url = base_url
        self._api_key = api_key
        self._sender_id = sender_id
        self._template_id = template_id

    async def send_sms(self, phone: str, message: str) -> None:
        import httpx

        payload = {
            "apikey": self._api_key,
            "sender": self._sender_id,
            "template_id": self._template_id,
            "to": normalize_phone(phone),
            "message": message,
        }
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(self._base_url, data=payload)
            if not resp.is_success:
                raise RuntimeError(
                    f"SMS gateway returned {resp.status_code}: {resp.text[:200]}"
                )


def build_sms_provider_from_config() -> Optional[SmsProvider]:
    """
    Construct the configured SMS provider, or ``None`` when SMS is not
    configured. A ``None`` provider makes the SMS tier report delivery failure
    rather than silently dropping the OTP.
    """
    choice = (settings.SMS_PROVIDER or "").strip().lower()
    if choice in ("", "none", "disabled"):
        return None
    if choice == "stub":
        return LoggingSmsProvider()
    if choice == "http":
        return HttpDltSmsProvider(
            base_url=settings.SMS_API_BASE_URL,
            api_key=settings.SMS_API_KEY,
            sender_id=settings.SMS_SENDER_ID,
            template_id=settings.SMS_DLT_TEMPLATE_ID,
        )
    logger.warning("Unknown SMS_PROVIDER=%r; treating SMS as unconfigured", choice)
    return None


class SmsSender:
    """
    Deliver an OTP by SMS through a swappable :class:`SmsProvider`.

    The provider is injected for tests, or resolved lazily from config in
    production. When no provider is configured, ``send`` returns a failed SMS
    attempt so :class:`TieredOtpSender` reports a delivery failure rather than
    marking an undelivered OTP as delivered (Requirement 3.4).
    """

    def __init__(self, provider: Optional[SmsProvider] = None):
        self._provider = provider
        self._provider_explicit = provider is not None

    def _get_provider(self) -> Optional[SmsProvider]:
        if self._provider_explicit:
            return self._provider
        # Resolve from config lazily so config changes are picked up and an
        # unconfigured environment does not fail at import time.
        return build_sms_provider_from_config()

    async def send(self, phone: str, code: str) -> DeliveryResult:
        provider = self._get_provider()
        if provider is None:
            return DeliveryResult(
                delivered=False,
                channel=None,
                attempts=[
                    DeliveryAttempt(
                        channel=DeliveryChannel.SMS,
                        confirmed=False,
                        error="no SMS provider configured",
                    )
                ],
            )
        message = format_otp_message(code)
        try:
            await provider.send_sms(phone, message)
        except Exception as exc:
            logger.warning("SMS OTP send failed: %s", exc)
            return DeliveryResult(
                delivered=False,
                channel=None,
                attempts=[
                    DeliveryAttempt(
                        channel=DeliveryChannel.SMS, confirmed=False, error=str(exc)
                    )
                ],
            )
        return DeliveryResult(
            delivered=True,
            channel=DeliveryChannel.SMS,
            attempts=[DeliveryAttempt(channel=DeliveryChannel.SMS, confirmed=True)],
        )


# ── Tiered orchestration ──────────────────────────────────────────────────────

class TieredOtpSender:
    """
    Channel-first, SMS-fallback OTP delivery (Requirement 3).

    Behaviour:
      - If the phone has an associated Notification_Channel, send over it and
        wait up to ``channel_timeout`` seconds for confirmation (Req 3.1).
      - If there is no channel, skip straight to SMS (Req 3.2).
      - If the channel send is not confirmed within its window (timeout or
        error), record the channel attempt as failed and fall back to SMS
        (Req 3.3).
      - If SMS is not confirmed within ``sms_timeout`` seconds, return a
        delivery-failure result with ``delivered=False`` so the caller does NOT
        mark the OTP delivered and can offer a retry (Req 3.4, 2.4).

    Both sub-senders are injected and can be mocked independently.
    """

    def __init__(
        self,
        channel_sender: NotificationChannelSender,
        sms_sender: SmsSender,
        channel_timeout: Optional[float] = None,
        sms_timeout: Optional[float] = None,
    ):
        self._channel_sender = channel_sender
        self._sms_sender = sms_sender
        self._channel_timeout = (
            channel_timeout
            if channel_timeout is not None
            else settings.OTP_CHANNEL_CONFIRMATION_TIMEOUT_SECONDS
        )
        self._sms_timeout = (
            sms_timeout
            if sms_timeout is not None
            else settings.OTP_SMS_CONFIRMATION_TIMEOUT_SECONDS
        )

    async def send(self, phone: str, code: str) -> DeliveryResult:
        attempts: List[DeliveryAttempt] = []

        # ── Tier 1: Notification_Channel (Req 3.1) ──
        channel = self._channel_sender.channel_for(phone)
        if channel is not None:
            result = await self._attempt(
                self._channel_sender, phone, code, self._channel_timeout, channel
            )
            attempts.extend(result.attempts)
            if result.delivered:
                return DeliveryResult(
                    delivered=True, channel=result.channel, attempts=attempts
                )
            # Req 3.3: channel unconfirmed → recorded as failed above; fall to SMS.

        # ── Tier 2: SMS (Req 3.2 direct, or Req 3.3 fallback) ──
        sms_result = await self._attempt(
            self._sms_sender, phone, code, self._sms_timeout, DeliveryChannel.SMS
        )
        attempts.extend(sms_result.attempts)
        if sms_result.delivered:
            return DeliveryResult(
                delivered=True, channel=DeliveryChannel.SMS, attempts=attempts
            )

        # ── Req 3.4: both tiers unconfirmed → delivery failure, not delivered ──
        return DeliveryResult(delivered=False, channel=None, attempts=attempts)

    @staticmethod
    async def _attempt(
        sender: OtpSender,
        phone: str,
        code: str,
        timeout: float,
        channel: DeliveryChannel,
    ) -> DeliveryResult:
        """
        Run one sub-sender under a confirmation timeout.

        A timeout is treated as an unconfirmed (failed) attempt for ``channel``,
        matching "does not receive a delivery confirmation within N seconds"
        (Req 3.3, 3.4). Any raised error is likewise recorded as a failed
        attempt so the tier log is complete.
        """
        try:
            return await asyncio.wait_for(sender.send(phone, code), timeout=timeout)
        except asyncio.TimeoutError:
            return DeliveryResult(
                delivered=False,
                channel=None,
                attempts=[
                    DeliveryAttempt(
                        channel=channel,
                        confirmed=False,
                        error=f"no delivery confirmation within {timeout}s",
                    )
                ],
            )
        except Exception as exc:
            return DeliveryResult(
                delivered=False,
                channel=None,
                attempts=[
                    DeliveryAttempt(channel=channel, confirmed=False, error=str(exc))
                ],
            )
