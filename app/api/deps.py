"""
Auth dependencies for the app-first REST API (`/api/v1`).

These FastAPI dependencies are the single choke point through which every
domain request is authenticated, authorized, and tenant-scoped. They replace
the booth router's naive ``_get_tenant_db`` (which derived the tenant from a
URL path parameter) with a model where **the tenant is derived strictly from
the authenticated user's device session token** — never from a path/query
parameter. This makes cross-tenant access structurally impossible for any
router that depends on :func:`get_tenant_db_for_user` (Req 19.2, 19.6).

Three dependencies are provided:

- :func:`get_current_user` — resolve the ``Device_Session_Token`` (from an
  ``Authorization: Bearer <token>`` header or an httpOnly cookie), hash it,
  look up the ``Device`` in the registry DB, confirm it is unexpired and
  unrevoked, resolve the owning ``User``, and return an :class:`AuthedUser`.
  Fails closed with 401 when the token is missing, unknown, expired, revoked,
  or cannot be resolved to a user with a valid role and tenant (Req 2.10,
  2.11, 5.8, 19.4, 19.5).

- :func:`require_owner` — a thin gate that raises 403 unless the resolved user
  holds the Owner role (Req 5.4, 5.6, 5.7).

- :func:`get_tenant_db_for_user` — yield a business-DB session opened for the
  token-derived tenant, mirroring the existing ``get_db(tenant_id)`` session
  pattern. Because the tenant comes from :func:`get_current_user`, every query
  a router runs against this session is tenant-scoped by construction
  (Req 19.2, 19.6).

All failures fail *closed*: any inability to resolve a token, user, role, or
tenant results in an authorization error and no protected data is returned
(Req 5.8, 19.4, 19.5). Token hashing is delegated to :mod:`app.auth.security`
so this module never touches raw crypto.

_Requirements: 2.10, 2.11, 5.6, 5.8, 19.2, 19.4, 19.5, 19.6_
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Generator, Optional
from uuid import UUID

from fastapi import Depends, Request
from sqlalchemy.orm import Session

from app.api.errors import ForbiddenError, UnauthorizedError
from app.auth import security
from app.auth.models_auth import Device, User
from app.database import get_db, get_registry_db

# Name of the httpOnly cookie that may carry the Device_Session_Token when the
# App is served same-origin and the token is not sent as a Bearer header.
DEVICE_TOKEN_COOKIE = "device_session"

# The only roles the system recognizes (Req 5.1). A resolved user whose role is
# not in this set cannot be trusted, so requests are failed closed (Req 5.8).
_VALID_ROLES = frozenset({"owner", "staff"})

# Bearer scheme prefix (case-insensitive) for the Authorization header.
_BEARER_PREFIX = "bearer "


@dataclass(frozen=True)
class AuthedUser:
    """
    The authenticated principal resolved once per request from a device token.

    Immutable so routers cannot accidentally mutate the identity mid-request.
    ``tenant_id`` is the *only* source of tenant scoping used downstream — it is
    derived from the token-resolved device/user, never from request input.
    """

    user_id: UUID
    tenant_id: UUID
    role: str
    device_id: UUID


def _extract_token(request: Request) -> Optional[str]:
    """
    Pull the Device_Session_Token from the request.

    Preference order (Req: token may arrive as Bearer or httpOnly cookie):

    1. ``Authorization: Bearer <token>`` header (case-insensitive scheme).
    2. The ``device_session`` httpOnly cookie.

    Returns the raw token, or ``None`` if neither source carries a non-empty
    token. Whitespace-only values are treated as absent.
    """
    header = request.headers.get("Authorization")
    if header:
        stripped = header.strip()
        if stripped.lower().startswith(_BEARER_PREFIX):
            token = stripped[len(_BEARER_PREFIX):].strip()
            if token:
                return token

    cookie = request.cookies.get(DEVICE_TOKEN_COOKIE)
    if cookie:
        cookie = cookie.strip()
        if cookie:
            return cookie

    return None


def get_current_user(request: Request) -> AuthedUser:
    """
    Resolve the request's Device_Session_Token to an :class:`AuthedUser`.

    Steps (all failing *closed* with 401 — no detail leaked, per Req 5.8/19.4):

    1. Extract the token from the Bearer header or httpOnly cookie. Missing →
       401 (Req 19.4).
    2. Hash the token (SHA-256) and look up the matching ``Device`` in the
       **registry DB**. Unknown hash → 401.
    3. Reject a revoked (``revoked_at`` set) or expired (``expires_at`` in the
       past) device — a device at the end of its validity requires fresh OTP
       verification (Req 2.11), and a still-valid token skips OTP (Req 2.10).
    4. Resolve the owning ``User``. Missing user, a role that is neither Owner
       nor Staff, or a missing tenant → 401 (fail closed when role/tenant
       cannot be resolved, Req 5.8, 19.5).

    Returns ``AuthedUser{user_id, tenant_id, role, device_id}``. The tenant is
    taken from the device/user, giving downstream dependencies a trusted,
    request-input-independent scope (Req 19.2, 19.6).
    """
    token = _extract_token(request)
    if not token:
        # Req 19.4: unauthenticated request → deny with an authorization error.
        raise UnauthorizedError()

    token_hash = security.hash_token_for_storage(token)

    reg_db: Session = next(get_registry_db())
    try:
        device = (
            reg_db.query(Device)
            .filter(Device.token_hash == token_hash)
            .first()
        )
        if device is None:
            # Unknown token — never seen or already deleted.
            raise UnauthorizedError()

        now = datetime.utcnow()

        # Req 2.11: a device past its validity window must re-verify via OTP.
        if device.expires_at is None or device.expires_at <= now:
            raise UnauthorizedError()

        # A revoked (logged-out) device is no longer trusted.
        if device.revoked_at is not None:
            raise UnauthorizedError()

        user = (
            reg_db.query(User)
            .filter(User.user_id == device.user_id)
            .first()
        )
        if user is None:
            # Device references a user that no longer exists — fail closed.
            raise UnauthorizedError()

        # Fail closed when the role cannot be resolved to a known role (Req 5.8).
        if user.role not in _VALID_ROLES:
            raise UnauthorizedError()

        # Derive the tenant strictly from the resolved user. If it cannot be
        # attributed to a tenant, deny (Req 19.5).
        tenant_id = user.tenant_id
        if tenant_id is None:
            raise UnauthorizedError()

        return AuthedUser(
            user_id=user.user_id,
            tenant_id=tenant_id,
            role=user.role,
            device_id=device.device_id,
        )
    finally:
        reg_db.close()


def require_owner(user: AuthedUser = Depends(get_current_user)) -> AuthedUser:
    """
    Gate a route to Owner-role users only (Req 5.4, 5.6, 5.7).

    Depends on :func:`get_current_user`, so an unauthenticated request is
    already rejected with 401 before this runs. If the authenticated user is
    not an Owner, raise 403 and return no protected data. Enforced on every
    request independently of any navigation hidden in the App (Req 5.6).
    """
    if user.role != "owner":
        raise ForbiddenError()
    return user


def get_tenant_db_for_user(
    user: AuthedUser = Depends(get_current_user),
) -> Generator[Session, None, None]:
    """
    Yield a business-DB session scoped to the authenticated user's tenant.

    The tenant comes from :func:`get_current_user` (i.e. from the device token),
    never from a path/query parameter, so every query a router runs against the
    yielded session is confined to the caller's tenant (Req 19.2, 19.6). This
    mirrors the existing ``get_db(tenant_id)`` session-opening pattern used by
    the booth router, closing the session when the request completes.
    """
    db = next(get_db(user.tenant_id))
    try:
        yield db
    finally:
        db.close()
