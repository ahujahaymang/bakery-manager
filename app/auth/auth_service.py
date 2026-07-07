"""
Auth_Service — phone + OTP device verification (app-first pivot).

This module implements the phone-number / one-time-passcode (OTP) leg of the
Auth_Service: the flow that turns an untrusted device into a trusted one holding
a long-lived Device_Session_Token (Requirement 2). The remaining Auth_Service
responsibilities — per-user PIN + lockout (Requirement 4), Owner-only user
management (Requirement 5), and Sell/Manage mode elevation (Requirement 6) — are
implemented in later tasks and are stubbed here so the class stays coherent.

Design placement notes:

- All auth entities (``User``, ``Device``, ``OtpChallenge``) live in the
  **registry DB**, because login must resolve ``phone → tenant → user`` *before*
  any per-tenant business DB can be opened. ``AuthService`` therefore takes a
  registry-DB ``Session`` in its constructor.
- All hashing / token generation / constant-time comparison is delegated to
  :mod:`app.auth.security`; this module never touches raw crypto.
- OTP delivery is delegated to an injected :class:`OtpSender` (the tiered
  channel→SMS sender is implemented separately in ``app/auth/otp_sender.py``).
  ``AuthService`` depends only on the small :class:`OtpSender` protocol below, so
  the two components evolve independently.
- Domain failures are raised as :class:`AuthError` subclasses (not HTTP errors).
  The API layer (``app/api/auth_router.py``) translates them into HTTP responses;
  keeping the service layer HTTP-agnostic preserves the existing layering where
  the API depends on the service, never the reverse.
"""

from __future__ import annotations

import re
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Optional, Protocol, runtime_checkable
from uuid import UUID

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.auth import security
from app.auth.models_auth import Device, OtpChallenge, User
from app.auth.webauthn_service import WebAuthnAuthenticationError, WebAuthnService
from app.config import settings


# ── Tunables (kept in one place; requirement clauses cited inline) ────────────

# OTP is 4–8 digits per Req 2.2. We mint a fixed 6-digit code (inside that
# range) — enough entropy given the 5-minute window and 5-attempt cap.
_OTP_DIGITS = 6

# Req 2.9: an OTP is valid for 5 minutes.
_OTP_VALIDITY = timedelta(minutes=5)

# Req 2.8: after 5 incorrect submissions the OTP is invalidated and the user
# must request a new one.
_MAX_OTP_ATTEMPTS = 5

# Req 2.6: the Device_Session_Token validity is a configured, fixed duration
# clamped to the inclusive 30–90 day range.
_DEVICE_TOKEN_MIN_DAYS = 30
_DEVICE_TOKEN_MAX_DAYS = 90

# Req 4.1/4.2: a PIN is 4–8 numeric (ASCII) digits. Anything else is rejected
# and stored nowhere. ``[0-9]`` (not ``\d``) keeps this to ASCII digits only,
# excluding unicode digit look-alikes.
_PIN_RE = re.compile(r"^[0-9]{4,8}$")

# Req 4.5: after 5 consecutive incorrect PINs, PIN entry is locked for 300s.
_MAX_PIN_ATTEMPTS = 5
_PIN_LOCKOUT = timedelta(seconds=300)

# Req 5.1: a User holds exactly one role, either Owner or Staff. These are the
# only role strings accepted by ``create_user`` and matched by ``require_owner``
# style checks; anything else is rejected.
_VALID_ROLES = frozenset({"owner", "staff"})

# Owner-only user management (Req 5.5, 5.7) accepts a display name that is
# non-empty after trimming and no longer than this bound. The bound just guards
# against unbounded storage; the exact value is not requirement-driven.
_MAX_NAME_LENGTH = 100

# Req 6.7: after 5 consecutive failed Manage_Mode elevation verifications, entry
# to Manage_Mode is locked for 30 seconds. This mirrors the PIN lockout shape
# (Req 4.5) but is a shorter, device-scoped window specific to mode elevation.
_MAX_MANAGE_ATTEMPTS = 5
_MANAGE_LOCKOUT = timedelta(seconds=30)

# Phone format (Req 2.3): an optional leading ``+`` followed by 8–15 digits,
# matching the E.164-normalized shape stored on ``User.phone`` and the 8–15
# digit customer-phone rule used elsewhere in the system.
_PHONE_RE = re.compile(r"^\+?\d{8,15}$")

# Characters stripped during phone normalization before validation/lookup.
_PHONE_STRIP_RE = re.compile(r"[\s\-().]")


# ── Manage_Mode elevation lockout state (Req 6.7) ─────────────────────────────
#
# Unlike PIN lockout — which is a per-*user* property persisted on the ``User``
# row (``pin_failed_count`` / ``pin_locked_until``, Req 4.5) — the Manage_Mode
# elevation lockout is a per-*device* property (Req 6.3, 6.7): it gates a shared
# tablet's transition from Sell_Mode to Manage_Mode regardless of which Owner
# credential is tried. The auth data model (task 1.1) has no device-level
# counter columns, so rather than change the schema we track this in a small
# process-local map keyed by ``device_id``.
#
# Tradeoff (documented deliberately): this state is in-memory and therefore
# process-local and non-durable — it resets on restart and is not shared across
# workers. That is acceptable for a short 30-second brute-force speed-bump on an
# interactive gesture (a restart is far slower than the window it enforces, and
# each device is served by a single app instance in this deployment). The
# durable, cross-request brute-force protections remain in the DB: the per-user
# 300s PIN lockout (Req 4.5) still applies to every PIN check performed here.
_manage_lockouts: dict[UUID, "_ManageLockout"] = {}


@dataclass
class _ManageLockout:
    """Per-device Manage_Mode elevation failure/lockout counters (Req 6.7)."""
    failed_count: int = 0
    locked_until: Optional[datetime] = None


# ── OTP delivery interface (implemented by app/auth/otp_sender.py) ────────────

@dataclass
class DeliveryResult:
    """
    Outcome of an OTP delivery attempt.

    ``success`` reports whether the code was delivered (and confirmed, for
    senders that wait for confirmation). ``channel`` records the tier that
    delivered it ("telegram" | "whatsapp" | "sms") for storage on the
    ``OtpChallenge`` and for observability.
    """
    success: bool
    channel: Optional[str] = None


@runtime_checkable
class OtpSender(Protocol):
    """
    Minimal delivery contract ``AuthService`` depends on.

    The concrete tiered sender (channel-first, SMS fallback) is implemented in
    ``app/auth/otp_sender.py`` (a later task). Any object exposing an awaitable
    ``send(phone, code)`` that returns an object with ``success`` / ``channel``
    attributes satisfies this protocol, so the two components stay decoupled.
    """

    async def send(self, phone: str, code: str) -> DeliveryResult: ...


# ── Result value objects ──────────────────────────────────────────────────────

@dataclass
class OtpRequestResult:
    """
    Output of :meth:`AuthService.request_otp`.

    Carries the identifiers the caller needs to correlate a subsequent
    verification, plus the delivery channel and expiry. The raw OTP itself is
    never returned — it exists only in the delivery message and as a salted hash
    in the ``OtpChallenge`` (Req 2.2, 2.5).
    """
    challenge_id: UUID
    phone: str
    delivery_channel: Optional[str]
    expires_at: datetime


@dataclass
class DeviceSession:
    """
    Output of :meth:`AuthService.verify_otp` on success.

    ``token`` is the **raw** Device_Session_Token, returned to the caller
    exactly once (only its SHA-256 hash is persisted, per Req 2.5/2.6). The
    remaining fields identify the trusted device and its validity window.
    """
    token: str
    device_id: UUID
    user_id: UUID
    tenant_id: UUID
    expires_at: datetime


# ── Auth domain errors ─────────────────────────────────────────────────────────

class AuthError(Exception):
    """Base class for Auth_Service domain failures (translated to HTTP by the API layer)."""


class InvalidPhoneError(AuthError):
    """Req 2.3 — the submitted phone number failed format validation; no OTP is generated."""


class UnknownPhoneError(AuthError):
    """The phone number resolves to no registered user, so no device session can be issued."""


class OtpDeliveryError(AuthError):
    """Req 2.4 / 3.4 — the OTP could not be delivered; it is not recorded as delivered."""


class OtpNotFoundError(AuthError):
    """No active OTP challenge exists for the phone number (verification cannot proceed)."""


class OtpExpiredError(AuthError):
    """Req 2.9 — the OTP was submitted after its 5-minute validity window elapsed."""


class OtpInvalidError(AuthError):
    """Req 2.7 — the submitted OTP did not match the generated one."""


class OtpMaxAttemptsError(AuthError):
    """Req 2.8 — the OTP was entered incorrectly 5 times and is now invalidated."""


class UnknownUserError(AuthError):
    """The ``user_id`` resolves to no registered user, so no PIN operation can proceed."""


class InvalidPinError(AuthError):
    """Req 4.2 — the submitted PIN is not 4–8 numeric digits; it is rejected and stored nowhere."""


class PinLockedError(AuthError):
    """
    Req 4.5 — PIN entry is currently locked out after 5 consecutive failures.

    Carries ``retry_after`` (seconds until the lockout window elapses) so the
    API layer can surface it (e.g. HTTP 429 with a retry hint). While locked,
    verification is refused without checking the PIN.
    """

    def __init__(self, message: str, retry_after: int):
        super().__init__(message)
        self.retry_after = retry_after


class PermissionDeniedError(AuthError):
    """
    Req 5.5 / 5.7 / 6.3 — the acting principal lacks the role required for the
    operation.

    Raised when a non-Owner attempts Owner-only user management, or when a
    Manage_Mode elevation is attempted with a credential that verifies but does
    not belong to an Owner-role user. The operation is refused *before* any
    record is created or changed, and no protected data is returned.
    """


class InvalidUserError(AuthError):
    """Req 5.1 — the submitted user name failed validation; no user is created."""


class InvalidRoleError(AuthError):
    """Req 5.1 — the submitted role is not exactly one of Owner or Staff; no user is created."""


class DuplicateUserError(AuthError):
    """
    Req 5.1 — a user with the same phone already exists in the Owner's tenant.

    Honors the ``UniqueConstraint("tenant_id", "phone")`` on ``User``: the new
    user is not created and the conflict is surfaced to the caller.
    """


class UnknownDeviceError(AuthError):
    """The ``device_id`` resolves to no active (unexpired, unrevoked) device session."""


class ManageLockedError(AuthError):
    """
    Req 6.7 — Manage_Mode elevation is currently locked out after 5 consecutive
    failures.

    Carries ``retry_after`` (seconds until the 30-second window elapses) so the
    API layer can surface it. While locked, elevation is refused without
    verifying the supplied credential.
    """

    def __init__(self, message: str, retry_after: int):
        super().__init__(message)
        self.retry_after = retry_after


# ── Auth service ────────────────────────────────────────────────────────────────

class AuthService:
    """
    Authenticates users and issues device sessions against the registry DB.

    This task implements the phone + OTP → device session flow
    (:meth:`request_otp`, :meth:`verify_otp`). PIN, user management, and mode
    elevation are added by later tasks.
    """

    def __init__(self, registry_db: Session, otp_sender: OtpSender):
        """
        Args:
            registry_db: Session bound to the registry DB, where ``User``,
                ``Device``, and ``OtpChallenge`` live.
            otp_sender: Delivery backend satisfying :class:`OtpSender`. Injected
                so delivery (tiered channel→SMS) is decoupled from OTP logic.
        """
        self._db = registry_db
        self._otp_sender = otp_sender

    # ── Phone + OTP → device session (Req 2) ──────────────────────────────────

    async def request_otp(self, phone: str) -> OtpRequestResult:
        """
        Validate a phone number, mint an OTP, store it, and deliver it.

        Steps (Req 2.1–2.3, 2.9):

        1. Normalize and format-validate the phone. On failure, raise
           :class:`InvalidPhoneError` and create no challenge (Req 2.3).
        2. Resolve the phone to a registered ``User`` in the registry DB. An
           unknown phone raises :class:`UnknownPhoneError` (no account to trust).
        3. Invalidate any earlier active challenge for the phone so only the
           newest OTP is live (supports the "request a new OTP" flow, Req 2.8).
        4. Generate a 4–8 digit OTP, store its salted hash with a 5-minute
           expiry (Req 2.2, 2.9), and delegate delivery to the injected sender.
        5. If delivery fails, remove the challenge so the OTP is not recorded as
           delivered, and raise :class:`OtpDeliveryError` (Req 2.4, 3.4).

        Returns an :class:`OtpRequestResult` (never the raw OTP).
        """
        normalized = self._normalize_phone(phone)
        if not self._is_valid_phone(normalized):
            # Req 2.3: reject invalid phone, generate no OTP challenge.
            raise InvalidPhoneError("Phone number failed format validation")

        user = self._resolve_user(normalized)
        if user is None:
            raise UnknownPhoneError("No registered user for this phone number")

        # Invalidate any earlier live challenge for this phone (single active OTP).
        self._invalidate_active_challenges(normalized)

        code = self._generate_otp()
        now = datetime.utcnow()
        challenge = OtpChallenge(
            phone=normalized,
            code_hash=security.hash_otp(code),
            expires_at=now + _OTP_VALIDITY,
            attempt_count=0,
            created_at=now,
        )
        self._db.add(challenge)
        self._db.commit()
        self._db.refresh(challenge)

        # Delegate delivery. The tiered sender decides channel vs SMS (Req 3).
        result = await self._otp_sender.send(normalized, code)
        delivered, channel = self._read_delivery_result(result)

        if not delivered:
            # Req 2.4 / 3.4: do not record the OTP as delivered.
            self._db.delete(challenge)
            self._db.commit()
            raise OtpDeliveryError("OTP could not be delivered")

        challenge.delivery_channel = channel
        self._db.commit()

        return OtpRequestResult(
            challenge_id=challenge.challenge_id,
            phone=normalized,
            delivery_channel=channel,
            expires_at=challenge.expires_at,
        )

    def verify_otp(self, phone: str, code: str, device_info: Optional[dict] = None) -> DeviceSession:
        """
        Verify a submitted OTP and, on success, issue a Device_Session_Token.

        Enforced rules:

        - **Attempt cap** (Req 2.8): a challenge that has already reached 5
          attempts is invalidated; further submissions raise
          :class:`OtpMaxAttemptsError`.
        - **Expiry** (Req 2.9): a submission after the 5-minute window raises
          :class:`OtpExpiredError`.
        - **Constant-time match** (Req 2.7): the code is compared against the
          stored salted hash via :mod:`app.auth.security`. A mismatch increments
          the attempt counter and raises :class:`OtpInvalidError`; the 5th
          mismatch additionally invalidates the challenge and raises
          :class:`OtpMaxAttemptsError`.
        - **Single use** (Req 2.5): on success the challenge is consumed so it
          cannot be replayed.
        - **Token validity** (Req 2.6): the issued token's expiry is clamped to
          the inclusive 30–90 day range from configuration.

        Args:
            phone: The phone number the OTP was sent to.
            code: The submitted one-time passcode.
            device_info: Optional metadata about the device (e.g. ``{"label": ...}``)
                stored on the ``Device`` row for display.

        Returns:
            A :class:`DeviceSession` whose ``token`` (raw) is returned once.
        """
        normalized = self._normalize_phone(phone)
        challenge = self._latest_active_challenge(normalized)
        if challenge is None:
            raise OtpNotFoundError("No active OTP challenge for this phone number")

        now = datetime.utcnow()

        # Req 2.9: reject an expired OTP.
        if now > challenge.expires_at:
            raise OtpExpiredError("OTP has expired")

        # Req 2.8: a challenge already at the attempt cap is dead.
        if challenge.attempt_count >= _MAX_OTP_ATTEMPTS:
            self._consume(challenge, now)
            raise OtpMaxAttemptsError("Too many incorrect attempts; request a new OTP")

        # Req 2.7: constant-time comparison against the stored salted hash.
        if not security.verify_otp(challenge.code_hash, code):
            challenge.attempt_count += 1
            if challenge.attempt_count >= _MAX_OTP_ATTEMPTS:
                # Req 2.8: 5th wrong attempt invalidates the OTP.
                self._consume(challenge, now)
                self._db.commit()
                raise OtpMaxAttemptsError("Too many incorrect attempts; request a new OTP")
            self._db.commit()
            raise OtpInvalidError("Incorrect OTP")

        # Success. Resolve the user again (challenge is phone-scoped).
        user = self._resolve_user(normalized)
        if user is None:
            raise UnknownPhoneError("No registered user for this phone number")

        # Req 2.5: single-use — consume the challenge before issuing the token.
        self._consume(challenge, now)

        session = self._issue_device_session(user, device_info or {}, now)
        self._db.commit()
        return session

    # ── Per-user PIN + consecutive-failure lockout (Req 4.1–4.5) ──────────────
    #
    # A PIN is a 4–8 digit per-user secret verified on-device (no OTP). Hashing
    # is delegated to app/auth/security.py (argon2id); this layer owns only the
    # validation, storage on ``User.pin_hash``, and the 300-second lockout after
    # 5 consecutive incorrect attempts.

    def set_pin(self, user_id: UUID, pin: str) -> None:
        """
        Validate and store a User's PIN as an argon2 hash (Req 4.1, 4.2).

        The PIN must be 4–8 numeric digits. Validation happens *before* any
        lookup or write, so an invalid PIN is rejected and **nothing is stored**
        (Req 4.2): a fresh :class:`InvalidPinError` is raised and neither the
        ``pin_hash`` nor the lockout counters are touched.

        On a valid PIN, the plaintext is hashed via :func:`security.hash_pin`
        (argon2id) and stored on ``User.pin_hash``; the raw PIN is never
        persisted. Setting a PIN clears any prior failure/lockout state so the
        user starts from a clean slate.

        Args:
            user_id: The user whose PIN is being set.
            pin: The candidate PIN.

        Raises:
            InvalidPinError: The PIN is not 4–8 numeric digits (nothing stored).
            UnknownUserError: No user exists for ``user_id``.
        """
        if not self._is_valid_pin(pin):
            # Req 4.2: reject and store nothing.
            raise InvalidPinError("PIN must be 4 to 8 numeric digits")

        user = self._get_user(user_id)
        user.pin_hash = security.hash_pin(pin)
        # A newly set PIN resets any prior failure/lockout state.
        user.pin_failed_count = 0
        user.pin_locked_until = None
        self._db.commit()

    def verify_pin(self, user_id: UUID, pin: str) -> bool:
        """
        Verify a candidate PIN, enforcing consecutive-failure lockout (Req 4.3–4.5).

        Behavior:

        - **Locked out** (Req 4.5): if the user is inside an active 300-second
          lockout window, the PIN is *not* checked and :class:`PinLockedError`
          is raised (carrying ``retry_after``). An elapsed lockout is cleared
          (counter reset) before proceeding.
        - **Match** (Req 4.3): returns ``True`` and resets the consecutive-failure
          counter. The stored hash is transparently upgraded if argon2 parameters
          have changed.
        - **Mismatch** (Req 4.4): returns ``False`` and leaves the stored PIN
          unchanged, incrementing the consecutive-failure counter. The 5th
          consecutive failure additionally arms a 300-second lockout (Req 4.5).

        A user with no PIN set can never match, so verification returns ``False``.

        Args:
            user_id: The user to authenticate.
            pin: The submitted PIN.

        Returns:
            ``True`` on a successful match, ``False`` on mismatch.

        Raises:
            PinLockedError: PIN entry is locked out; the PIN is not checked.
            UnknownUserError: No user exists for ``user_id``.
        """
        user = self._get_user(user_id)
        now = datetime.utcnow()

        # Req 4.5: while locked, refuse without checking the PIN.
        if user.pin_locked_until is not None:
            if now < user.pin_locked_until:
                retry_after = int((user.pin_locked_until - now).total_seconds()) + 1
                raise PinLockedError("PIN entry is locked out", retry_after=retry_after)
            # Lockout window elapsed — clear it and start fresh (Req 4.5).
            user.pin_locked_until = None
            user.pin_failed_count = 0

        # Req 4.3: correct PIN grants access and resets the failure counter.
        if security.verify_pin(user.pin_hash, pin):
            user.pin_failed_count = 0
            user.pin_locked_until = None
            # Opportunistically upgrade the stored hash if the cost changed.
            if security.pin_hash_needs_rehash(user.pin_hash):
                user.pin_hash = security.hash_pin(pin)
            self._db.commit()
            return True

        # Req 4.4: mismatch denies, leaves the stored PIN unchanged, and counts
        # toward the consecutive-failure lockout threshold.
        user.pin_failed_count += 1
        if user.pin_failed_count >= _MAX_PIN_ATTEMPTS:
            # Req 4.5: 5th consecutive failure locks PIN entry for 300 seconds.
            user.pin_locked_until = now + _PIN_LOCKOUT
        self._db.commit()
        return False

    # ── User management (Owner-only, Req 5.1, 5.5, 5.7) ──────────────────────

    def create_user(self, owner: Any, name: str, phone: str, role: str) -> User:
        """
        Create a new ``User`` in the acting Owner's tenant (Owner-only).

        The ``owner`` argument is the authenticated principal making the request
        (the API layer's ``AuthedUser``). To avoid a hard dependency on the API
        layer, it is accepted duck-typed: any object exposing ``.role``,
        ``.tenant_id``, and ``.user_id`` is accepted.

        Behavior (validated *before* anything is persisted, so a rejected request
        leaves all ``User`` records unchanged, per Req 5.7):

        1. **Owner gate** (Req 5.5, 5.7): if ``owner.role`` is not ``"owner"``,
           raise :class:`PermissionDeniedError` and create nothing.
        2. **Field validation** (Req 5.1): the name must be non-empty after
           trimming (:class:`InvalidUserError`); the role must be exactly one of
           ``"owner"`` / ``"staff"`` (:class:`InvalidRoleError`); the phone must
           pass the shared normalize/format rules (:class:`InvalidPhoneError`).
        3. **Tenant binding** (Req 5.5): the new user is bound to the Owner's
           ``tenant_id`` — a created user always belongs to the creating Owner's
           tenant.
        4. **Uniqueness** (Req 5.1): honoring ``UniqueConstraint("tenant_id",
           "phone")``, a phone already present in the tenant raises
           :class:`DuplicateUserError` (checked up front and, defensively, on the
           DB integrity error should a concurrent insert race in).

        Args:
            owner: The authenticated Owner principal (duck-typed).
            name: Display name for the new user.
            phone: The new user's phone number (normalized before storage).
            role: Exactly one of ``"owner"`` or ``"staff"``.

        Returns:
            The persisted :class:`User`.

        Raises:
            PermissionDeniedError: The acting principal is not an Owner.
            InvalidUserError: The name is empty/too long.
            InvalidRoleError: The role is not Owner or Staff.
            InvalidPhoneError: The phone failed format validation.
            DuplicateUserError: The tenant already has a user with this phone.
        """
        self._require_owner(owner)
        tenant_id = self._principal_tenant(owner)

        clean_name = name.strip() if isinstance(name, str) else ""
        if not clean_name or len(clean_name) > _MAX_NAME_LENGTH:
            # Req 5.1: a user must have a valid name; store nothing on failure.
            raise InvalidUserError("User name must be non-empty")

        if role not in _VALID_ROLES:
            # Req 5.1: exactly one of Owner or Staff.
            raise InvalidRoleError("Role must be exactly one of 'owner' or 'staff'")

        normalized = self._normalize_phone(phone)
        if not self._is_valid_phone(normalized):
            raise InvalidPhoneError("Phone number failed format validation")

        # Req 5.1: reject a phone that already exists in this tenant up front, so
        # the common conflict never reaches (and poisons) the DB transaction.
        existing = (
            self._db.query(User)
            .filter(User.tenant_id == tenant_id, User.phone == normalized)
            .first()
        )
        if existing is not None:
            raise DuplicateUserError("A user with this phone already exists in the tenant")

        user = User(
            tenant_id=tenant_id,
            name=clean_name,
            phone=normalized,
            role=role,
            created_at=datetime.utcnow(),
        )
        self._db.add(user)
        try:
            self._db.commit()
        except IntegrityError as exc:
            # Defensive: a concurrent insert won the (tenant_id, phone) race.
            self._db.rollback()
            raise DuplicateUserError(
                "A user with this phone already exists in the tenant"
            ) from exc
        self._db.refresh(user)
        return user

    def list_users(self, owner: Any) -> list[User]:
        """
        List the users belonging to the acting Owner's tenant (Owner-only).

        The ``owner`` principal is accepted duck-typed (see :meth:`create_user`).
        If it does not hold the Owner role, :class:`PermissionDeniedError` is
        raised and no data is returned (Req 5.7). Results are scoped to the
        Owner's ``tenant_id`` (Req 5.5) and ordered by creation time for a
        stable listing.

        Args:
            owner: The authenticated Owner principal (duck-typed).

        Returns:
            The tenant's users, oldest first.

        Raises:
            PermissionDeniedError: The acting principal is not an Owner.
        """
        self._require_owner(owner)
        tenant_id = self._principal_tenant(owner)
        return (
            self._db.query(User)
            .filter(User.tenant_id == tenant_id)
            .order_by(User.created_at.asc())
            .all()
        )

    # ── Mode transitions & user switching (Req 4.9, 6.3–6.7) ─────────────────

    def elevate_to_manage(self, device_id: UUID, credential: Any) -> bool:
        """
        Verify a PIN or WebAuthn credential to enter Manage_Mode or switch users.

        No OTP is involved (Req 4.9, 6.6): a device already trusted via OTP
        (Req 2) re-verifies a *user* credential here. The ``credential`` payload
        identifies which user is being verified and how, and is accepted
        duck-typed (dict or object) with these fields:

        - ``user_id`` (required): the user to verify/switch to.
        - ``pin`` (optional): a candidate PIN, verified via :meth:`verify_pin`.
        - ``webauthn`` (optional): a WebAuthn assertion payload, verified via
          :class:`WebAuthnService`; requires ``expected_challenge`` alongside it.
        - ``purpose`` (optional): ``"manage"`` (default) to elevate Sell_Mode →
          Manage_Mode, or ``"switch"`` to switch the active user.

        **Manage_Mode elevation** (``purpose="manage"``, Req 6.3, 6.6, 6.7):

        - While the device is inside an active 30-second lockout, the credential
          is *not* checked and :class:`ManageLockedError` is raised (Req 6.7).
        - The credential must verify *and* the target user must hold the Owner
          role. A verified non-Owner credential is refused with
          :class:`PermissionDeniedError` (Req 6.3) and does not count as a
          verification failure.
        - A failed verification increments the device's consecutive-failure
          counter and returns ``False`` (App stays in Sell_Mode, Req 6.6); the
          5th consecutive failure arms the 30-second lockout (Req 6.7). A
          successful Owner elevation returns ``True`` and clears the counter.

        **User switching** (``purpose="switch"``, Req 4.9, 6.6): verifying the
        PIN or WebAuthn credential of *any* registered user on the device
        switches the active user without OTP and without a role requirement.
        Returns ``True`` on success, ``False`` on a failed verification. (The
        per-user 300s PIN lockout from :meth:`verify_pin` still applies.)

        The target user must belong to the device's tenant; otherwise
        :class:`PermissionDeniedError` is raised.

        Args:
            device_id: The trusted device requesting the transition.
            credential: The verification payload (see above).

        Returns:
            ``True`` if the credential verified (and, for ``"manage"``, the user
            is an Owner); ``False`` on a failed credential verification.

        Raises:
            UnknownDeviceError: No active device session for ``device_id``.
            UnknownUserError: The target ``user_id`` resolves to no user.
            PermissionDeniedError: Target user is not in the device's tenant, or
                (for ``"manage"``) verified but not an Owner.
            ManageLockedError: Manage_Mode elevation is locked out (Req 6.7).
            PinLockedError: The target user's PIN entry is locked (Req 4.5).
        """
        device = self._get_active_device(device_id)
        target_user_id = self._coerce_uuid(self._cred_field(credential, "user_id"))
        purpose = self._cred_field(credential, "purpose") or "manage"
        is_manage = purpose != "switch"

        now = datetime.utcnow()
        if is_manage:
            # Req 6.7: refuse without checking the credential while locked out.
            self._check_manage_lockout(device_id, now)

        user = self._get_user(target_user_id)
        # The target user must belong to the same tenant as the trusted device;
        # cross-tenant elevation/switching is never permitted (Req 19 scoping).
        if user.tenant_id != device.tenant_id:
            raise PermissionDeniedError("User does not belong to this device's tenant")

        verified = self._verify_credential(user, credential)

        if not verified:
            if is_manage:
                # Req 6.6/6.7: failed elevation keeps Sell_Mode and counts toward
                # the 30-second lockout.
                self._record_manage_failure(device_id, now)
            return False

        if is_manage:
            # Req 6.3: Manage_Mode entry requires an Owner-role user. A valid
            # non-Owner credential is a permission problem, not a verification
            # failure, so it does not arm the lockout.
            if user.role != "owner":
                raise PermissionDeniedError("Manage_Mode requires an Owner-role user")
            # Req 6.7: a successful elevation clears the failure counter.
            self._reset_manage_lockout(device_id)

        return True

    # ── Internal helpers ─────────────────────────────────────────────────────

    @staticmethod
    def _normalize_phone(phone: str) -> str:
        """Strip spaces and common separators, preserving a leading ``+``."""
        if not phone:
            return ""
        return _PHONE_STRIP_RE.sub("", phone.strip())

    @staticmethod
    def _is_valid_phone(normalized_phone: str) -> bool:
        """Return whether the normalized phone matches the accepted format (Req 2.3)."""
        return bool(_PHONE_RE.match(normalized_phone))

    @staticmethod
    def _is_valid_pin(pin: Any) -> bool:
        """Return whether ``pin`` is 4–8 ASCII numeric digits (Req 4.1, 4.2)."""
        return isinstance(pin, str) and bool(_PIN_RE.match(pin))

    def _get_user(self, user_id: UUID) -> User:
        """
        Resolve a ``user_id`` to its ``User`` row, or raise :class:`UnknownUserError`.

        Used by the PIN operations, which are addressed by user id (the caller is
        already an authenticated device bound to a user).
        """
        user = self._db.query(User).filter(User.user_id == user_id).first()
        if user is None:
            raise UnknownUserError("No registered user for this id")
        return user

    @staticmethod
    def _require_owner(principal: Any) -> None:
        """
        Fail closed unless the principal holds the Owner role (Req 5.5, 5.7).

        Accepts the principal duck-typed (any object exposing ``.role``) so the
        service does not import the API layer's ``AuthedUser``. A missing or
        non-``"owner"`` role raises :class:`PermissionDeniedError`.
        """
        if getattr(principal, "role", None) != "owner":
            raise PermissionDeniedError("This operation requires the Owner role")

    @staticmethod
    def _principal_tenant(principal: Any) -> UUID:
        """
        Extract the acting principal's ``tenant_id``, failing closed if absent.

        A principal without a resolvable tenant cannot scope a user-management
        operation, so we refuse rather than guess (Req 5.5, 19).
        """
        tenant_id = getattr(principal, "tenant_id", None)
        if tenant_id is None:
            raise PermissionDeniedError("Acting user has no resolvable tenant")
        return tenant_id

    def _get_active_device(self, device_id: UUID) -> Device:
        """
        Resolve a ``device_id`` to an active (unexpired, unrevoked) ``Device``.

        A missing, revoked, or expired device session cannot ground a
        Manage_Mode elevation or a user switch, so :class:`UnknownDeviceError`
        is raised.
        """
        device = self._db.query(Device).filter(Device.device_id == device_id).first()
        if device is None:
            raise UnknownDeviceError("No device session for this id")
        now = datetime.utcnow()
        if device.revoked_at is not None or now > device.expires_at:
            raise UnknownDeviceError("Device session is revoked or expired")
        return device

    def _verify_credential(self, user: User, credential: Any) -> bool:
        """
        Verify the PIN or WebAuthn credential carried in ``credential`` for ``user``.

        Exactly one of ``pin`` or ``webauthn`` is expected. PIN checks route
        through :meth:`verify_pin` (so the per-user 300s lockout of Req 4.5
        applies and may raise :class:`PinLockedError`). WebAuthn checks delegate
        to :class:`WebAuthnService`; an assertion failure is treated as a failed
        verification (``False``) rather than an exception so callers get a
        uniform boolean regardless of credential type (Req 4.7, 4.8).

        Raises:
            InvalidUserError: Neither a PIN nor a WebAuthn payload was supplied.
        """
        pin = self._cred_field(credential, "pin")
        if pin is not None:
            return self.verify_pin(user.user_id, pin)

        assertion = self._cred_field(credential, "webauthn")
        if assertion is not None:
            expected_challenge = self._cred_field(credential, "expected_challenge")
            webauthn = WebAuthnService(self._db)
            try:
                webauthn.finish_authentication(user.user_id, assertion, expected_challenge)
                return True
            except WebAuthnAuthenticationError:
                # Req 4.8: a WebAuthn failure denies access.
                return False

        raise InvalidUserError("A PIN or WebAuthn credential is required")

    # ── Manage_Mode lockout helpers (Req 6.7) ────────────────────────────────

    @staticmethod
    def _check_manage_lockout(device_id: UUID, now: datetime) -> None:
        """
        Raise :class:`ManageLockedError` if the device is inside its 30-second
        Manage_Mode lockout window; clear an elapsed window in passing (Req 6.7).
        """
        state = _manage_lockouts.get(device_id)
        if state is None or state.locked_until is None:
            return
        if now < state.locked_until:
            retry_after = int((state.locked_until - now).total_seconds()) + 1
            raise ManageLockedError("Manage_Mode elevation is locked out", retry_after=retry_after)
        # Window elapsed — reset so verification can be attempted again (Req 6.7).
        _manage_lockouts.pop(device_id, None)

    @staticmethod
    def _record_manage_failure(device_id: UUID, now: datetime) -> None:
        """
        Count a failed Manage_Mode elevation and arm the 30-second lockout on the
        5th consecutive failure (Req 6.7).
        """
        state = _manage_lockouts.setdefault(device_id, _ManageLockout())
        state.failed_count += 1
        if state.failed_count >= _MAX_MANAGE_ATTEMPTS:
            state.locked_until = now + _MANAGE_LOCKOUT

    @staticmethod
    def _reset_manage_lockout(device_id: UUID) -> None:
        """Clear a device's Manage_Mode failure/lockout state after a success (Req 6.7)."""
        _manage_lockouts.pop(device_id, None)

    @staticmethod
    def _cred_field(credential: Any, key: str) -> Any:
        """
        Read ``key`` from a duck-typed credential payload.

        Accepts either a mapping (``credential[key]``) or an object exposing the
        field as an attribute (``credential.key``), returning ``None`` when the
        field is absent. This keeps the service decoupled from any particular
        request/DTO shape.
        """
        if isinstance(credential, dict):
            return credential.get(key)
        return getattr(credential, key, None)

    @staticmethod
    def _coerce_uuid(value: Any) -> UUID:
        """
        Coerce a credential's ``user_id`` (``UUID`` or string) to a ``UUID``.

        A missing or unparseable id cannot identify a user, so
        :class:`UnknownUserError` is raised (mirroring an unknown user id).
        """
        if isinstance(value, UUID):
            return value
        try:
            return UUID(str(value))
        except (ValueError, AttributeError, TypeError) as exc:
            raise UnknownUserError("No registered user for this id") from exc

    @staticmethod
    def _generate_otp() -> str:
        """Generate a zero-padded numeric OTP with :data:`_OTP_DIGITS` digits (Req 2.2)."""
        upper = 10 ** _OTP_DIGITS
        return f"{secrets.randbelow(upper):0{_OTP_DIGITS}d}"

    def _resolve_user(self, normalized_phone: str) -> Optional[User]:
        """
        Resolve a phone number to its registered ``User``.

        ``(tenant_id, phone)`` is unique, so a phone can in principle belong to
        more than one tenant; the earliest-created match is chosen
        deterministically. In practice the phone maps to a single owner/staff
        user.
        """
        return (
            self._db.query(User)
            .filter(User.phone == normalized_phone)
            .order_by(User.created_at.asc())
            .first()
        )

    def _invalidate_active_challenges(self, normalized_phone: str) -> None:
        """Consume any still-live challenges for a phone so only the newest OTP is valid."""
        now = datetime.utcnow()
        active = (
            self._db.query(OtpChallenge)
            .filter(
                OtpChallenge.phone == normalized_phone,
                OtpChallenge.consumed_at.is_(None),
            )
            .all()
        )
        for challenge in active:
            challenge.consumed_at = now
        if active:
            self._db.commit()

    def _latest_active_challenge(self, normalized_phone: str) -> Optional[OtpChallenge]:
        """Return the most recent unconsumed challenge for a phone, if any."""
        return (
            self._db.query(OtpChallenge)
            .filter(
                OtpChallenge.phone == normalized_phone,
                OtpChallenge.consumed_at.is_(None),
            )
            .order_by(OtpChallenge.created_at.desc())
            .first()
        )

    def _consume(self, challenge: OtpChallenge, when: datetime) -> None:
        """Mark a challenge consumed (single-use / invalidation, Req 2.5, 2.8)."""
        if challenge.consumed_at is None:
            challenge.consumed_at = when

    @staticmethod
    def _clamp_session_days(days: int) -> int:
        """Clamp a configured session length to the inclusive 30–90 day range (Req 2.6)."""
        return max(_DEVICE_TOKEN_MIN_DAYS, min(_DEVICE_TOKEN_MAX_DAYS, days))

    def _issue_device_session(self, user: User, device_info: dict, now: datetime) -> DeviceSession:
        """
        Mint a Device_Session_Token, persist its hash as a ``Device``, and
        return the raw token exactly once (Req 2.5, 2.6).
        """
        raw_token = security.generate_device_token()
        days = self._clamp_session_days(settings.OTP_SESSION_DAYS)
        expires_at = now + timedelta(days=days)

        device = Device(
            user_id=user.user_id,
            tenant_id=user.tenant_id,
            token_hash=security.hash_token_for_storage(raw_token),
            label=device_info.get("label") if isinstance(device_info, dict) else None,
            issued_at=now,
            expires_at=expires_at,
        )
        self._db.add(device)
        self._db.flush()  # populate device_id without ending the transaction

        return DeviceSession(
            token=raw_token,
            device_id=device.device_id,
            user_id=user.user_id,
            tenant_id=user.tenant_id,
            expires_at=expires_at,
        )

    @staticmethod
    def _read_delivery_result(result: Any) -> tuple[bool, Optional[str]]:
        """
        Interpret an OtpSender return value defensively.

        Kept duck-typed so ``AuthService`` stays decoupled from the concrete
        sender. The production sender (``app/auth/otp_sender.py``) reports the
        outcome via a ``delivered`` flag and a ``DeliveryChannel`` enum
        ``channel``; the local :class:`DeliveryResult` uses ``success`` /
        ``str`` instead. We accept either:

        - **Delivered flag**: prefer ``delivered`` (the otp_sender contract),
          fall back to ``success``, and only as a last resort treat a bare
          truthy return as success. This ensures a real ``delivered=False``
          failure is honored and the OTP is *not* recorded as delivered
          (Req 2.4 / 3.4).
        - **Channel**: normalize a ``DeliveryChannel`` (a ``str`` Enum) to its
          plain string ``value`` so it stores cleanly in
          ``OtpChallenge.delivery_channel``.
        """
        if hasattr(result, "delivered"):
            delivered = bool(result.delivered)
        elif hasattr(result, "success"):
            delivered = bool(result.success)
        else:
            delivered = bool(result)

        channel = getattr(result, "channel", None)
        if channel is not None:
            channel = getattr(channel, "value", channel)
        return delivered, channel
