"""
Auth domain router for the app-first REST API (``/api/v1/auth``).

A thin HTTP adapter over :class:`~app.auth.auth_service.AuthService` and
:class:`~app.auth.webauthn_service.WebAuthnService`. Unlike the domain routers
(which open a per-tenant *business* DB via ``get_tenant_db_for_user``), auth
operates against the **registry DB**: login must resolve ``phone → tenant →
user`` before any tenant file can be opened, so ``User`` / ``Device`` /
``OtpChallenge`` / ``WebAuthnCredential`` all live in the registry DB (design
§3). Every service here is therefore constructed against ``get_registry_db``.

Endpoint map (design §5 — Auth rows):

| Method + path                         | Service call                     | Gate   |
|---------------------------------------|----------------------------------|--------|
| ``POST /otp/request``                 | ``AuthService.request_otp``      | public |
| ``POST /otp/verify``                  | ``AuthService.verify_otp``       | public |
| ``POST /pin``                         | ``AuthService.set_pin``          | authed |
| ``POST /pin/verify``                  | ``AuthService.verify_pin``       | authed |
| ``POST /webauthn/register/options``   | ``WebAuthnService.start_registration``  | authed |
| ``POST /webauthn/register``           | ``WebAuthnService.finish_registration`` | authed |
| ``POST /webauthn/verify/options``     | ``WebAuthnService.start_authentication``| authed |
| ``POST /webauthn/verify``             | ``WebAuthnService.finish_authentication``| authed |
| ``GET  /users``                       | ``AuthService.list_users``       | Owner  |
| ``POST /users``                       | ``AuthService.create_user``      | Owner  |
| ``POST /mode/manage``                 | ``AuthService.elevate_to_manage``| authed |

The service layer stays HTTP-agnostic and signals failures via
:class:`~app.auth.auth_service.AuthError` subclasses (and the WebAuthn errors);
:func:`map_auth_errors` translates those into the shared HTTP error bodies
defined in :mod:`app.api.errors` (401/403/400/409/429/502).

The raw Device_Session_Token minted by ``verify_otp`` is returned in the
response body exactly once *and* set as an httpOnly cookie so the App can reopen
without re-sending the Bearer token (design §Security — token delivery).

_Requirements: 2.1, 2.5, 4.1, 4.3, 4.6, 4.7, 5.5, 5.7, 6.3_
"""

from __future__ import annotations

import contextlib
import json
from datetime import datetime
from typing import Any, Awaitable, Callable, Dict, List, Optional

from fastapi import APIRouter, Body, Depends, Request, Response, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from webauthn.helpers import base64url_to_bytes, bytes_to_base64url

from app.api import errors
from app.api.deps import (
    DEVICE_TOKEN_COOKIE,
    AuthedUser,
    get_current_user,
    require_owner,
)
from app.auth import auth_service as auth_errors
from app.auth.auth_service import AuthService
from app.auth.models_auth import User
from app.auth.otp_sender import (
    NotificationChannelSender,
    RegistryChannelResolver,
    SmsSender,
    TieredOtpSender,
)
from app.auth.webauthn_service import (
    WebAuthnAuthenticationError,
    WebAuthnRegistrationError,
    WebAuthnService,
)
from app.database import get_registry_db

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])

# httpOnly cookies that carry the pending WebAuthn challenge between the
# ``*/options`` (start) call and its matching ``finish`` call. The WebAuthn
# service is deliberately stateless about the challenge (design §4), so the
# router persists it here: the challenge minted by ``start_*`` must be replayed
# verbatim to ``finish_*`` as ``expected_challenge``.
WEBAUTHN_REG_CHALLENGE_COOKIE = "webauthn_reg_challenge"
WEBAUTHN_AUTH_CHALLENGE_COOKIE = "webauthn_auth_challenge"


# ── Request bodies ─────────────────────────────────────────────────────────────

class OtpRequestBody(BaseModel):
    """Body for ``POST /otp/request`` (Req 2.1)."""

    phone: str = Field(min_length=1)


class OtpVerifyBody(BaseModel):
    """Body for ``POST /otp/verify`` (Req 2.5)."""

    phone: str = Field(min_length=1)
    code: str = Field(min_length=1)
    label: Optional[str] = None


class PinBody(BaseModel):
    """Body for ``POST /pin`` and ``POST /pin/verify`` (Req 4.1, 4.3)."""

    pin: str = Field(min_length=1)


class UserCreateBody(BaseModel):
    """Body for ``POST /users`` — Owner-only user creation (Req 5.5)."""

    name: str = Field(min_length=1)
    phone: str = Field(min_length=1)
    role: str = Field(min_length=1)


class ManageElevateBody(BaseModel):
    """
    Body for ``POST /mode/manage`` (Req 6.3).

    Identifies the user whose Owner credential is being verified and how. Exactly
    one of ``pin`` / ``webauthn`` is expected. ``purpose`` defaults to ``manage``
    (elevate Sell_Mode → Manage_Mode); ``switch`` switches the active user
    without a role requirement (Req 4.9).
    """

    user_id: str = Field(min_length=1)
    pin: Optional[str] = None
    webauthn: Optional[Dict[str, Any]] = None
    purpose: Optional[str] = None


# ── Dependencies ───────────────────────────────────────────────────────────────

# Module-level Telegram OTP send hook. The running ``TelegramBotListener``
# registers its Bot-API send function here at startup (see
# ``telegram_listener._start_webhook_server``), so the app-first ``/auth`` OTP
# path can deliver the code over the owner's existing Telegram channel (Req 3.1).
# It stays ``None`` when the API runs without the bot (web-only dev, tests), in
# which case OTP delivery falls back to SMS.
_telegram_otp_send: Optional[Callable[[str, str], Awaitable[None]]] = None


def set_telegram_otp_send(fn: Optional[Callable[[str, str], Awaitable[None]]]) -> None:
    """Register (or clear) the Telegram send path used for OTP delivery."""
    global _telegram_otp_send
    _telegram_otp_send = fn


def _build_otp_sender() -> TieredOtpSender:
    """
    Assemble the tiered (Notification_Channel → SMS) OTP sender (Req 3).

    The channel tier resolves phone → channel from the registry DB. When the
    running bot has registered a Telegram send path via
    :func:`set_telegram_otp_send`, a known owner's OTP is delivered over their
    Telegram channel (Req 3.1); otherwise the channel tier reports "no usable
    channel" and delivery falls through to SMS (whose concrete provider is
    selected lazily from config). This keeps the router functional whether or
    not the live bot is wired in.
    """
    resolver = RegistryChannelResolver(lambda: next(get_registry_db()))
    channel_sender = NotificationChannelSender(
        resolver, telegram_send=_telegram_otp_send
    )
    return TieredOtpSender(channel_sender, SmsSender())


def get_auth_service(db: Session = Depends(get_registry_db)) -> AuthService:
    """Construct an :class:`AuthService` bound to the registry DB and OTP sender."""
    return AuthService(db, _build_otp_sender())


def get_webauthn_service(db: Session = Depends(get_registry_db)) -> WebAuthnService:
    """Construct a :class:`WebAuthnService` bound to the registry DB."""
    return WebAuthnService(db)


# ── Error mapping ──────────────────────────────────────────────────────────────

@contextlib.contextmanager
def map_auth_errors():
    """
    Translate ``AuthService`` / ``WebAuthnService`` domain errors into the shared
    HTTP error bodies (:mod:`app.api.errors`).

    Mapping (design error table):

    - invalid phone / PIN / user / role      → 400 validation_error (with field)
    - duplicate user phone                    → 409 conflict
    - unknown phone / user                    → 404 not_found
    - OTP not-found / expired / wrong,
      unknown device, WebAuthn assertion fail → 401 unauthorized
    - permission denied (non-Owner)           → 403 forbidden
    - OTP max-attempts, PIN / Manage lockout  → 429 locked_out (retry_after)
    - OTP delivery failure (both tiers)       → 502 otp_delivery_failed
    - WebAuthn attestation failure            → 400 validation_error
    """
    try:
        yield
    except errors.APIError:
        raise
    except auth_errors.InvalidPhoneError as exc:
        raise errors.ValidationError(detail=str(exc), field="phone") from exc
    except auth_errors.InvalidPinError as exc:
        raise errors.ValidationError(detail=str(exc), field="pin") from exc
    except auth_errors.InvalidUserError as exc:
        raise errors.ValidationError(detail=str(exc), field="name") from exc
    except auth_errors.InvalidRoleError as exc:
        raise errors.ValidationError(detail=str(exc), field="role") from exc
    except auth_errors.DuplicateUserError as exc:
        raise errors.ConflictError(field="phone", detail=str(exc)) from exc
    except (auth_errors.UnknownPhoneError, auth_errors.UnknownUserError) as exc:
        raise errors.NotFoundError() from exc
    except auth_errors.OtpDeliveryError as exc:
        raise errors.OtpDeliveryFailedError() from exc
    except auth_errors.OtpMaxAttemptsError as exc:
        # Too many OTP attempts — the challenge is dead; a new OTP is required.
        raise errors.LockedOutError(detail=str(exc)) from exc
    except auth_errors.PinLockedError as exc:
        raise errors.LockedOutError(retry_after=exc.retry_after) from exc
    except auth_errors.ManageLockedError as exc:
        raise errors.LockedOutError(retry_after=exc.retry_after) from exc
    except (
        auth_errors.OtpNotFoundError,
        auth_errors.OtpExpiredError,
        auth_errors.OtpInvalidError,
        auth_errors.UnknownDeviceError,
    ) as exc:
        raise errors.UnauthorizedError() from exc
    except auth_errors.PermissionDeniedError as exc:
        raise errors.ForbiddenError() from exc
    except WebAuthnRegistrationError as exc:
        raise errors.ValidationError(detail=str(exc), field="credential") from exc
    except WebAuthnAuthenticationError as exc:
        # Req 4.8: a failed WebAuthn assertion denies access.
        raise errors.UnauthorizedError() from exc


# ── Serialization helpers ────────────────────────────────────────────────────

def _serialize_user(user: User) -> Dict[str, Any]:
    """Serialize a ``User`` row (no secret/financial fields)."""
    return {
        "user_id": user.user_id,
        "tenant_id": user.tenant_id,
        "name": user.name,
        "phone": user.phone,
        "role": user.role,
        "created_at": user.created_at,
    }


# ── OTP → device session (public, Req 2) ───────────────────────────────────────

@router.post("/otp/request")
async def request_otp(
    body: OtpRequestBody,
    svc: AuthService = Depends(get_auth_service),
) -> Dict[str, Any]:
    """
    Validate a phone number, mint an OTP, and deliver it (Req 2.1).

    Public: this is the entry point for a device with no valid session. An
    invalid phone is rejected with 400 and no OTP is minted (Req 2.3); a
    delivery failure on every tier is surfaced as 502 so the App can offer a
    retry (Req 2.4/3.4). The raw OTP is never returned.
    """
    with map_auth_errors():
        result = await svc.request_otp(body.phone)
    return {
        "challenge_id": result.challenge_id,
        "phone": result.phone,
        "delivery_channel": result.delivery_channel,
        "expires_at": result.expires_at,
    }


@router.post("/otp/verify")
def verify_otp(
    body: OtpVerifyBody,
    response: Response,
    svc: AuthService = Depends(get_auth_service),
    db: Session = Depends(get_registry_db),
) -> Dict[str, Any]:
    """
    Verify a submitted OTP and issue a Device_Session_Token (Req 2.5).

    Public. On success the raw token is returned in the body **once** and also
    set as an httpOnly cookie so the App can reopen without re-sending it. A
    wrong/expired/consumed OTP yields 401; the 5th wrong attempt yields 429
    (request a new OTP).
    """
    with map_auth_errors():
        session = svc.verify_otp(
            body.phone,
            body.code,
            device_info={"label": body.label} if body.label else None,
        )

    max_age = max(0, int((session.expires_at - datetime.utcnow()).total_seconds()))
    response.set_cookie(
        key=DEVICE_TOKEN_COOKIE,
        value=session.token,
        max_age=max_age,
        httponly=True,
        secure=True,
        samesite="lax",
    )
    # Include the resolved user so the App can populate the current user and
    # apply role-aware UI (tab visibility, mode) immediately after sign-in.
    user_row = db.query(User).filter(User.user_id == session.user_id).first()
    return {
        "token": session.token,
        "device_id": session.device_id,
        "user_id": session.user_id,
        "tenant_id": session.tenant_id,
        "expires_at": session.expires_at,
        "user": _serialize_user(user_row) if user_row is not None else None,
    }


# ── Session restore (authenticated, Req 2.10) ───────────────────────────────────

@router.get("/session")
def current_session(
    user: AuthedUser = Depends(get_current_user),
    db: Session = Depends(get_registry_db),
) -> Dict[str, Any]:
    """
    Return the user for the device's existing Device_Session_Token (Req 2.10).

    While a device holds a valid token it must be able to reopen the App without
    re-verifying an OTP. The App calls this on startup with its persisted Bearer
    token (or httpOnly cookie): a valid token resolves to the current user so the
    App can rehydrate the session and apply role-aware UI; an invalid/expired
    token fails closed with 401 via ``get_current_user``, prompting fresh OTP.
    """
    row = db.query(User).filter(User.user_id == user.user_id).first()
    if row is None:
        raise errors.UnauthorizedError()
    return {"user": _serialize_user(row)}


# ── PIN (authenticated, Req 4.1–4.5) ────────────────────────────────────────────

@router.post("/pin", status_code=status.HTTP_204_NO_CONTENT)
def set_pin(
    body: PinBody,
    user: AuthedUser = Depends(get_current_user),
    svc: AuthService = Depends(get_auth_service),
) -> Response:
    """
    Set the authenticated user's PIN (Req 4.1).

    The PIN is validated (4–8 numeric digits) and stored only as an argon2 hash;
    an invalid PIN is rejected with 400 and nothing is stored (Req 4.2).
    """
    with map_auth_errors():
        svc.set_pin(user.user_id, body.pin)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/pin/verify")
def verify_pin(
    body: PinBody,
    user: AuthedUser = Depends(get_current_user),
    svc: AuthService = Depends(get_auth_service),
) -> Dict[str, Any]:
    """
    Verify the authenticated user's PIN (Req 4.3).

    Returns ``{"verified": true|false}``. A mismatch is a normal negative result
    (Req 4.4), not an HTTP error; 5 consecutive failures arm a 300-second
    lockout surfaced as 429 with ``retry_after`` (Req 4.5).
    """
    with map_auth_errors():
        verified = svc.verify_pin(user.user_id, body.pin)
    return {"verified": verified}


# ── WebAuthn (authenticated, Req 4.6–4.8) ────────────────────────────────────

@router.post("/webauthn/register/options")
def webauthn_register_options(
    response: Response,
    user: AuthedUser = Depends(get_current_user),
    db: Session = Depends(get_registry_db),
    svc: WebAuthnService = Depends(get_webauthn_service),
) -> Dict[str, Any]:
    """
    Begin WebAuthn registration: build attestation options (Req 4.6).

    Returns the ``PublicKeyCredentialCreationOptions`` for
    ``navigator.credentials.create()`` and stashes the freshly minted challenge
    in an httpOnly cookie to be replayed to ``POST /webauthn/register``.
    """
    row = db.query(User).filter(User.user_id == user.user_id).first()
    if row is None:
        raise errors.UnauthorizedError()

    opts = svc.start_registration(
        user_id=user.user_id,
        user_name=row.phone or row.name,
        user_display_name=row.name,
    )
    response.set_cookie(
        key=WEBAUTHN_REG_CHALLENGE_COOKIE,
        value=bytes_to_base64url(opts.challenge),
        httponly=True,
        secure=True,
        samesite="lax",
    )
    return json.loads(opts.options_json)


@router.post("/webauthn/register")
def webauthn_register(
    request: Request,
    response: Response,
    credential: Dict[str, Any] = Body(...),
    user: AuthedUser = Depends(get_current_user),
    svc: WebAuthnService = Depends(get_webauthn_service),
) -> Dict[str, Any]:
    """
    Finish WebAuthn registration: verify the attestation and persist the
    credential as an alternative to the PIN (Req 4.6).
    """
    challenge_b64 = request.cookies.get(WEBAUTHN_REG_CHALLENGE_COOKIE)
    if not challenge_b64:
        raise errors.ValidationError(
            detail="missing WebAuthn registration challenge", field="challenge"
        )

    with map_auth_errors():
        registered = svc.finish_registration(
            user_id=user.user_id,
            credential=credential,
            expected_challenge=base64url_to_bytes(challenge_b64),
        )

    response.delete_cookie(WEBAUTHN_REG_CHALLENGE_COOKIE)
    return {
        "credential_id": registered.credential_id,
        "user_id": registered.user_id,
        "sign_count": registered.sign_count,
    }


@router.post("/webauthn/verify/options")
def webauthn_verify_options(
    response: Response,
    user: AuthedUser = Depends(get_current_user),
    svc: WebAuthnService = Depends(get_webauthn_service),
) -> Dict[str, Any]:
    """
    Begin WebAuthn authentication: build assertion options scoped to the user's
    registered credentials (Req 4.7). The challenge is stashed in an httpOnly
    cookie to be replayed to ``POST /webauthn/verify``.
    """
    opts = svc.start_authentication(user_id=user.user_id)
    response.set_cookie(
        key=WEBAUTHN_AUTH_CHALLENGE_COOKIE,
        value=bytes_to_base64url(opts.challenge),
        httponly=True,
        secure=True,
        samesite="lax",
    )
    return json.loads(opts.options_json)


@router.post("/webauthn/verify")
def webauthn_verify(
    request: Request,
    response: Response,
    credential: Dict[str, Any] = Body(...),
    user: AuthedUser = Depends(get_current_user),
    svc: WebAuthnService = Depends(get_webauthn_service),
) -> Dict[str, Any]:
    """
    Finish WebAuthn authentication: verify the assertion and grant access
    (Req 4.7). A failed assertion is denied with 401 (Req 4.8).
    """
    challenge_b64 = request.cookies.get(WEBAUTHN_AUTH_CHALLENGE_COOKIE)
    if not challenge_b64:
        raise errors.ValidationError(
            detail="missing WebAuthn authentication challenge", field="challenge"
        )

    with map_auth_errors():
        result = svc.finish_authentication(
            user_id=user.user_id,
            credential=credential,
            expected_challenge=base64url_to_bytes(challenge_b64),
        )

    response.delete_cookie(WEBAUTHN_AUTH_CHALLENGE_COOKIE)
    return {
        "verified": True,
        "user_id": result.user_id,
        "credential_id": result.credential_id,
        "user_verified": result.user_verified,
    }


# ── User management (Owner-only, Req 5.5, 5.7) ──────────────────────────────────

@router.get("/users")
def list_users(
    owner: AuthedUser = Depends(require_owner),
    svc: AuthService = Depends(get_auth_service),
) -> List[Dict[str, Any]]:
    """
    List the Owner's tenant users (Owner-only, Req 5.7).

    ``require_owner`` rejects a Staff/unauthenticated caller with 403/401 before
    the service runs; results are scoped to the Owner's tenant (Req 5.5).
    """
    with map_auth_errors():
        users = svc.list_users(owner)
    return [_serialize_user(u) for u in users]


@router.post("/users", status_code=status.HTTP_201_CREATED)
def create_user(
    body: UserCreateBody,
    owner: AuthedUser = Depends(require_owner),
    svc: AuthService = Depends(get_auth_service),
) -> Dict[str, Any]:
    """
    Create a user in the Owner's tenant (Owner-only, Req 5.5, 5.7).

    The new user is bound to the acting Owner's tenant. Field failures are 400
    (naming the field); a phone already present in the tenant is 409 conflict
    with the existing record left unchanged.
    """
    with map_auth_errors():
        created = svc.create_user(owner, body.name, body.phone, body.role)
    return _serialize_user(created)


# ── Mode elevation (authenticated, Req 6.3) ─────────────────────────────────────

@router.post("/mode/manage")
def elevate_to_manage(
    body: ManageElevateBody,
    request: Request,
    user: AuthedUser = Depends(get_current_user),
    svc: AuthService = Depends(get_auth_service),
) -> Dict[str, Any]:
    """
    Elevate Sell_Mode → Manage_Mode with an Owner PIN/WebAuthn credential (Req 6.3).

    The device is taken from the authenticated session (never request input).
    Returns ``{"elevated": true, "mode": "manage"}`` on success. A failed
    credential is a normal negative result (App stays in Sell_Mode, Req 6.6):
    ``{"elevated": false, "mode": "sell"}``. A valid *non-Owner* credential is a
    403; 5 consecutive failures arm a 30-second lockout surfaced as 429 with
    ``retry_after`` (Req 6.7).
    """
    credential: Dict[str, Any] = {
        "user_id": body.user_id,
        "purpose": body.purpose or "manage",
    }
    if body.pin is not None:
        credential["pin"] = body.pin
    if body.webauthn is not None:
        credential["webauthn"] = body.webauthn
        # A WebAuthn credential re-verified here replays the assertion challenge
        # minted by POST /webauthn/verify/options (stashed in the cookie).
        challenge_b64 = request.cookies.get(WEBAUTHN_AUTH_CHALLENGE_COOKIE)
        credential["expected_challenge"] = (
            base64url_to_bytes(challenge_b64) if challenge_b64 else b""
        )

    with map_auth_errors():
        elevated = svc.elevate_to_manage(user.device_id, credential)

    return {"elevated": elevated, "mode": "manage" if elevated else "sell"}
