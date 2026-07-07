"""
Shared HTTP error mapping for the app-first REST API.

The service layer signals domain errors with plain ``ValueError`` and the auth
layer raises the typed errors defined here. This module gives the API a single,
consistent way to turn both into HTTP responses whose status codes and body
shapes match the design's error table:

| Condition                                   | Status | Body                                                     |
|---------------------------------------------|--------|----------------------------------------------------------|
| Missing/invalid/expired device token        | 401    | {"error": "unauthorized"}                                |
| Authenticated but role not permitted         | 403    | {"error": "forbidden", "detail": "insufficient ..."}    |
| Cross-tenant access attempt                  | 403    | {"error": "forbidden"}                                   |
| Validation failure (field range/format)      | 400    | {"error": "validation_error", "field": ..., "detail":..}|
| Duplicate (e.g. customer phone)              | 409    | {"error": "conflict", "field": ...}                     |
| Not found (order/item)                       | 404    | {"error": "not_found"}                                   |
| Illegal order state transition               | 409    | {"error": "invalid_transition"}                          |
| Image extraction failure                     | 422    | {"error": "extraction_failed"}                           |
| OTP delivery failure (both tiers)            | 502    | {"error": "otp_delivery_failed"}                         |
| Lockout active (PIN / Manage elevation)      | 429    | {"error": "locked_out", "retry_after": <seconds>}       |

Usage in a router:

    from app.api import errors

    @router.post("/customers")
    def create_customer(...):
        with errors.map_service_errors():
            return svc.create_customer(...)   # ValueError -> proper HTTP error

Auth code raises the typed errors directly:

    raise errors.UnauthorizedError()
    raise errors.LockedOutError(retry_after=300)

Register the handlers once on the FastAPI app:

    from app.api.errors import register_error_handlers
    register_error_handlers(app)

_Requirements: 5.4, 5.8, 9.9, 12.3, 13.5, 15.9, 3.4_
"""

from __future__ import annotations

import contextlib
import logging
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


# ── Typed API errors ─────────────────────────────────────────────────────────

class APIError(Exception):
    """
    Base class for all app-first API errors.

    Each subclass fixes an HTTP ``status_code`` and knows how to render its
    JSON body via :meth:`to_body`. Routers and auth code raise these directly;
    a FastAPI exception handler (see :func:`register_error_handlers`) turns them
    into the matching HTTP response.
    """

    status_code: int = 500
    error_code: str = "error"

    def __init__(self, detail: Optional[str] = None) -> None:
        self.detail = detail
        super().__init__(detail or self.error_code)

    def to_body(self) -> Dict[str, Any]:
        body: Dict[str, Any] = {"error": self.error_code}
        if self.detail:
            body["detail"] = self.detail
        return body


class UnauthorizedError(APIError):
    """401 — device token missing, unknown, revoked, or expired (Req 5.8)."""

    status_code = 401
    error_code = "unauthorized"

    def to_body(self) -> Dict[str, Any]:
        # 401 body is intentionally minimal to avoid disclosing detail.
        return {"error": self.error_code}


class ForbiddenError(APIError):
    """403 — authenticated but role/tenant not permitted (Req 5.4)."""

    status_code = 403
    error_code = "forbidden"

    def __init__(self, detail: Optional[str] = "insufficient permissions") -> None:
        super().__init__(detail)


class ValidationError(APIError):
    """400 — a field failed range/format validation (Req 9.7, 10.2, 12.4, ...)."""

    status_code = 400
    error_code = "validation_error"

    def __init__(self, detail: Optional[str] = None, field: Optional[str] = None) -> None:
        super().__init__(detail)
        self.field = field

    def to_body(self) -> Dict[str, Any]:
        body: Dict[str, Any] = {"error": self.error_code}
        if self.field:
            body["field"] = self.field
        if self.detail:
            body["detail"] = self.detail
        return body


class ConflictError(APIError):
    """409 — a uniqueness constraint was violated, e.g. duplicate phone (Req 12.3)."""

    status_code = 409
    error_code = "conflict"

    def __init__(self, field: Optional[str] = None, detail: Optional[str] = None) -> None:
        super().__init__(detail)
        self.field = field

    def to_body(self) -> Dict[str, Any]:
        body: Dict[str, Any] = {"error": self.error_code}
        if self.field:
            body["field"] = self.field
        if self.detail:
            body["detail"] = self.detail
        return body


class NotFoundError(APIError):
    """404 — the requested resource does not exist (Req 13.5)."""

    status_code = 404
    error_code = "not_found"

    def to_body(self) -> Dict[str, Any]:
        return {"error": self.error_code}


class InvalidTransitionError(APIError):
    """409 — an illegal order status transition was requested (Req 9.9)."""

    status_code = 409
    error_code = "invalid_transition"

    def to_body(self) -> Dict[str, Any]:
        return {"error": self.error_code}


class ExtractionFailedError(APIError):
    """422 — image extraction could not produce a draft (Req 15.9)."""

    status_code = 422
    error_code = "extraction_failed"

    def to_body(self) -> Dict[str, Any]:
        return {"error": self.error_code}


class OtpDeliveryFailedError(APIError):
    """502 — OTP could not be delivered on any tier; not marked delivered (Req 3.4)."""

    status_code = 502
    error_code = "otp_delivery_failed"

    def to_body(self) -> Dict[str, Any]:
        return {"error": self.error_code}


class AgentUnavailableError(APIError):
    """
    502 — the LLM/agent backing the Ask/Insights chat could not produce an
    answer (network failure, missing API key, or an unexpected exception in the
    agent/tool path). The failure detail is logged server-side; the response
    body is intentionally minimal so no stack trace or internal detail leaks.
    """

    status_code = 502
    error_code = "agent_unavailable"

    def to_body(self) -> Dict[str, Any]:
        return {"error": self.error_code}


class LockedOutError(APIError):
    """429 — PIN entry or Manage_Mode elevation is locked out (Req 4.5, 6.7)."""

    status_code = 429
    error_code = "locked_out"

    def __init__(self, retry_after: Optional[int] = None, detail: Optional[str] = None) -> None:
        super().__init__(detail)
        self.retry_after = retry_after

    def to_body(self) -> Dict[str, Any]:
        body: Dict[str, Any] = {"error": self.error_code}
        if self.retry_after is not None:
            body["retry_after"] = self.retry_after
        return body

    def headers(self) -> Dict[str, str]:
        if self.retry_after is not None:
            return {"Retry-After": str(self.retry_after)}
        return {}


# ── ValueError translation ────────────────────────────────────────────────────
#
# The service layer raises ``ValueError`` with human-readable messages. We
# classify those messages the same way ``app/error_handler.py`` does, so the
# HTTP layer and the Telegram layer stay consistent.

# Substrings that indicate the ValueError names a specific field. Used to
# surface a ``field`` in validation/conflict bodies for the app UI.
_FIELD_HINTS = (
    "phone",
    "name",
    "email",
    "quantity",
    "price",
    "amount",
    "category",
    "delivery_date",
    "delivery date",
    "date",
    "yield",
    "description",
)


def _extract_field(message: str) -> Optional[str]:
    """Best-effort: pull a field name out of a validation/conflict message."""
    lowered = message.lower()
    for hint in _FIELD_HINTS:
        if hint in lowered:
            # Normalize "delivery date" -> "delivery_date" for API consumers.
            return hint.replace(" ", "_")
    return None


def translate_value_error(exc: ValueError) -> APIError:
    """
    Translate a service-layer ``ValueError`` into the matching :class:`APIError`.

    Classification is message-based (the service layer does not use typed
    exceptions), mirroring ``ErrorHandler._classify``:

    - "not found" / "does not exist"                 -> 404 not_found
    - "already exists" / "duplicate" / "taken"        -> 409 conflict
    - "transition" / illegal status change            -> 409 invalid_transition
    - "extraction" / "could not read image"           -> 422 extraction_failed
    - anything else that looks like validation         -> 400 validation_error
    """
    message = str(exc)
    lowered = message.lower()

    if "not found" in lowered or "does not exist" in lowered:
        return NotFoundError(detail=message)

    if any(k in lowered for k in ("already exists", "duplicate", "already taken", "is taken", "already registered")):
        return ConflictError(field=_extract_field(message), detail=message)

    if "transition" in lowered or "cannot be delivered" in lowered or "cannot be cancelled" in lowered:
        return InvalidTransitionError()

    if "extraction" in lowered or "could not extract" in lowered or "could not read" in lowered:
        return ExtractionFailedError()

    # Default: treat as a field validation failure (Req 9.7, 10.2, 11.5, 12.4,
    # 13.6, 14.2, 15.8). This is the safest default for a domain ValueError.
    return ValidationError(detail=message, field=_extract_field(message))


@contextlib.contextmanager
def map_service_errors():
    """
    Context manager that converts service-layer ``ValueError`` into the proper
    :class:`APIError`. Typed :class:`APIError` raised inside the block pass
    through unchanged.

        with map_service_errors():
            return svc.create_customer(...)
    """
    try:
        yield
    except APIError:
        raise
    except ValueError as exc:
        raise translate_value_error(exc) from exc


# ── FastAPI integration ────────────────────────────────────────────────────────

def register_error_handlers(app) -> None:
    """
    Register exception handlers on a FastAPI ``app`` so raised :class:`APIError`
    (and any uncaught service-layer ``ValueError``) render as the designed JSON
    bodies. Call this once from ``register_api()``.
    """
    from fastapi import Request
    from fastapi.responses import JSONResponse

    @app.exception_handler(APIError)
    async def _handle_api_error(request: "Request", exc: APIError):  # noqa: ANN001
        headers = exc.headers() if isinstance(exc, LockedOutError) else None
        return JSONResponse(status_code=exc.status_code, content=exc.to_body(), headers=headers)

    @app.exception_handler(ValueError)
    async def _handle_value_error(request: "Request", exc: ValueError):  # noqa: ANN001
        # Defense-in-depth: any ValueError that escapes a router is still mapped
        # to a consistent body instead of a bare 500.
        api_error = translate_value_error(exc)
        return JSONResponse(status_code=api_error.status_code, content=api_error.to_body())
