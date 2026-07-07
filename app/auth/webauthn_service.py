"""
WebAuthn (passkey) service for the app-first pivot Auth_Service.

This module wraps the `py_webauthn <https://pypi.org/project/webauthn/>`_
library (imported as ``webauthn``) to implement the two WebAuthn ceremonies the
App needs, offering biometric / platform-authenticator unlock as an alternative
to the per-user PIN (Requirement 4.6, 4.7, 4.8):

1. **Registration (attestation).** ``start_registration`` produces the
   ``PublicKeyCredentialCreationOptions`` the browser feeds to
   ``navigator.credentials.create()``; ``finish_registration`` verifies the
   authenticator's attestation response and persists a new
   :class:`~app.auth.models_auth.WebAuthnCredential` (credential id, public key,
   initial sign count) for the user (Requirement 4.6).

2. **Authentication (assertion).** ``start_authentication`` produces the
   ``PublicKeyCredentialRequestOptions`` for ``navigator.credentials.get()``
   scoped to the user's registered credentials; ``finish_authentication``
   verifies the assertion against the stored credential, updates the stored
   ``sign_count`` (clone-detection), and grants access on success
   (Requirement 4.7). On any verification failure it raises
   :class:`WebAuthnAuthenticationError` (Requirement 4.8).

**Challenge handling.** WebAuthn is a two-step, stateful ceremony: the challenge
minted by the ``start_*`` call must be replayed to the matching ``finish_*``
call. This service is deliberately stateless with respect to the challenge — it
returns the freshly generated challenge to the caller (the auth router), which
stashes it in the short-lived session/state and passes it back as
``expected_challenge``. Keeping challenge storage out of this service avoids
coupling it to any particular session backend.

**Encoding.** The authenticator hands back the credential id and public key as
raw bytes. Both are stored as url-safe base64 (base64url) strings —
``WebAuthnCredential.credential_id`` is the base64url credential id (primary
key) and ``WebAuthnCredential.public_key`` is the base64url public key — and
decoded back to bytes when a stored credential is used to verify an assertion.

Relying Party (RP) identity — RP ID, RP name, and the expected origin — comes
from application config (``WEBAUTHN_RP_ID`` / ``WEBAUTHN_RP_NAME`` /
``WEBAUTHN_ORIGIN``), defaulting to the production ``kitchenos.info`` origin.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, List, Optional, Union
from uuid import UUID

from sqlalchemy.orm import Session

from webauthn import (
    generate_authentication_options,
    generate_registration_options,
    options_to_json,
    verify_authentication_response,
    verify_registration_response,
)
from webauthn.helpers import base64url_to_bytes, bytes_to_base64url
from webauthn.helpers.exceptions import (
    InvalidAuthenticationResponse,
    InvalidRegistrationResponse,
)
from webauthn.helpers.structs import (
    AuthenticatorSelectionCriteria,
    PublicKeyCredentialDescriptor,
    ResidentKeyRequirement,
    UserVerificationRequirement,
)

from app.auth.models_auth import WebAuthnCredential
from app.config import settings

logger = logging.getLogger(__name__)


# ── Errors ───────────────────────────────────────────────────────────────────

class WebAuthnError(Exception):
    """Base class for WebAuthn ceremony failures raised by this service."""


class WebAuthnRegistrationError(WebAuthnError):
    """Raised when an attestation (registration) response fails verification."""


class WebAuthnAuthenticationError(WebAuthnError):
    """
    Raised when an assertion (authentication) response fails verification —
    unknown credential, bad signature, or clone/sign-count anomaly.

    Surfaced to the caller so the auth router can deny access and report a
    WebAuthn authentication failure (Requirement 4.8).
    """


# ── Result value objects ──────────────────────────────────────────────────────

@dataclass
class RegistrationOptions:
    """
    Output of :meth:`WebAuthnService.start_registration`.

    ``options_json`` is the JSON string to hand to the browser's
    ``navigator.credentials.create()``. ``challenge`` is the raw challenge the
    caller must persist and replay to :meth:`WebAuthnService.finish_registration`
    as ``expected_challenge``.
    """
    options_json: str
    challenge: bytes


@dataclass
class RegisteredCredential:
    """Output of :meth:`WebAuthnService.finish_registration` — the persisted credential."""
    credential_id: str          # base64url
    user_id: UUID
    sign_count: int


@dataclass
class AuthenticationOptions:
    """
    Output of :meth:`WebAuthnService.start_authentication`.

    ``options_json`` is the JSON string for ``navigator.credentials.get()``.
    ``challenge`` is the raw challenge to persist and replay to
    :meth:`WebAuthnService.finish_authentication` as ``expected_challenge``.
    """
    options_json: str
    challenge: bytes


@dataclass
class AuthenticationResult:
    """Output of :meth:`WebAuthnService.finish_authentication` on success."""
    user_id: UUID
    credential_id: str          # base64url
    new_sign_count: int
    user_verified: bool


# Accepted shapes for a credential coming back from the browser: the raw JSON
# string, a decoded dict, or an already-parsed py_webauthn struct.
CredentialInput = Union[str, Dict[str, Any]]


class WebAuthnService:
    """
    WebAuthn registration and authentication ceremonies backed by the registry DB.

    A :class:`WebAuthnCredential` row is stored per registered authenticator
    (``credential_id`` base64url PK, ``public_key`` base64url, ``sign_count``).
    The service reads RP identity from config but accepts overrides for testing.
    """

    def __init__(
        self,
        registry_db: Session,
        *,
        rp_id: Optional[str] = None,
        rp_name: Optional[str] = None,
        origin: Optional[str] = None,
        require_user_verification: bool = False,
    ):
        """
        Args:
            registry_db: Session bound to the registry DB (where auth models live).
            rp_id: Relying Party ID (registrable domain suffix of the origin).
                Defaults to ``settings.WEBAUTHN_RP_ID``.
            rp_name: Human-readable RP name shown by the authenticator.
                Defaults to ``settings.WEBAUTHN_RP_NAME``.
            origin: Exact expected origin (scheme+host+port) the browser sends.
                Defaults to ``settings.WEBAUTHN_ORIGIN``.
            require_user_verification: When ``True``, verification requires the
                authenticator to have performed user verification (biometric /
                PIN gesture). Left ``False`` by default so roaming authenticators
                without UV still work; the auth router may opt into stricter UV.
        """
        self._db = registry_db
        self._rp_id = rp_id or settings.WEBAUTHN_RP_ID
        self._rp_name = rp_name or settings.WEBAUTHN_RP_NAME
        self._origin = origin or settings.WEBAUTHN_ORIGIN
        self._require_uv = require_user_verification

    # ── Registration (attestation) ───────────────────────────────────────────

    def start_registration(
        self,
        user_id: UUID,
        user_name: str,
        user_display_name: Optional[str] = None,
    ) -> RegistrationOptions:
        """
        Build the attestation options for ``navigator.credentials.create()``.

        The user's UUID is used as the WebAuthn user handle. Any credentials the
        user has already registered are advertised in ``exclude_credentials`` so
        the same authenticator is not enrolled twice.

        Returns the options JSON plus the raw challenge, which the caller must
        persist and replay to :meth:`finish_registration` (Requirement 4.6).
        """
        exclude = [
            PublicKeyCredentialDescriptor(id=base64url_to_bytes(cred.credential_id))
            for cred in self._user_credentials(user_id)
        ]

        options = generate_registration_options(
            rp_id=self._rp_id,
            rp_name=self._rp_name,
            user_id=user_id.bytes,
            user_name=user_name,
            user_display_name=user_display_name or user_name,
            exclude_credentials=exclude or None,
            authenticator_selection=AuthenticatorSelectionCriteria(
                resident_key=ResidentKeyRequirement.PREFERRED,
                user_verification=UserVerificationRequirement.PREFERRED,
            ),
        )
        return RegistrationOptions(
            options_json=options_to_json(options),
            challenge=options.challenge,
        )

    def finish_registration(
        self,
        user_id: UUID,
        credential: CredentialInput,
        expected_challenge: bytes,
    ) -> RegisteredCredential:
        """
        Verify an attestation response and persist a new WebAuthnCredential.

        On success the credential id (base64url), public key (base64url), and
        initial sign count are stored for the user and the persisted record is
        returned (Requirement 4.6). Raises :class:`WebAuthnRegistrationError` if
        the attestation fails verification.
        """
        try:
            verification = verify_registration_response(
                credential=credential,
                expected_challenge=expected_challenge,
                expected_rp_id=self._rp_id,
                expected_origin=self._origin,
                require_user_verification=self._require_uv,
            )
        except InvalidRegistrationResponse as exc:
            logger.info("WebAuthn registration verification failed for user %s: %s", user_id, exc)
            raise WebAuthnRegistrationError(str(exc)) from exc

        credential_id_b64 = bytes_to_base64url(verification.credential_id)
        public_key_b64 = bytes_to_base64url(verification.credential_public_key)

        existing = (
            self._db.query(WebAuthnCredential)
            .filter(WebAuthnCredential.credential_id == credential_id_b64)
            .one_or_none()
        )
        if existing is not None:
            # Re-registration of the same authenticator: refresh key/count/owner
            # rather than creating a duplicate primary key.
            existing.user_id = user_id
            existing.public_key = public_key_b64
            existing.sign_count = verification.sign_count
            record = existing
        else:
            record = WebAuthnCredential(
                credential_id=credential_id_b64,
                user_id=user_id,
                public_key=public_key_b64,
                sign_count=verification.sign_count,
                created_at=datetime.utcnow(),
            )
            self._db.add(record)

        self._db.commit()
        logger.info("Registered WebAuthn credential %s for user %s", credential_id_b64, user_id)
        return RegisteredCredential(
            credential_id=record.credential_id,
            user_id=user_id,
            sign_count=record.sign_count,
        )

    # ── Authentication (assertion) ───────────────────────────────────────────

    def start_authentication(self, user_id: UUID) -> AuthenticationOptions:
        """
        Build the assertion options for ``navigator.credentials.get()`` scoped
        to the user's registered credentials.

        Returns the options JSON plus the raw challenge, which the caller must
        persist and replay to :meth:`finish_authentication` (Requirement 4.7).
        """
        allow = [
            PublicKeyCredentialDescriptor(id=base64url_to_bytes(cred.credential_id))
            for cred in self._user_credentials(user_id)
        ]

        options = generate_authentication_options(
            rp_id=self._rp_id,
            allow_credentials=allow or None,
            user_verification=UserVerificationRequirement.PREFERRED,
        )
        return AuthenticationOptions(
            options_json=options_to_json(options),
            challenge=options.challenge,
        )

    def finish_authentication(
        self,
        user_id: UUID,
        credential: CredentialInput,
        expected_challenge: bytes,
    ) -> AuthenticationResult:
        """
        Verify an assertion response against the user's stored credential and
        update its sign count.

        Looks up the stored credential referenced by the response, verifies the
        signature and challenge/origin, and — on success — persists the new
        sign count and returns an :class:`AuthenticationResult` (Requirement 4.7).

        Raises :class:`WebAuthnAuthenticationError` if the credential is unknown,
        does not belong to the user, or fails verification (Requirement 4.8).
        """
        credential_id_b64 = self._extract_credential_id(credential)

        record = (
            self._db.query(WebAuthnCredential)
            .filter(WebAuthnCredential.credential_id == credential_id_b64)
            .one_or_none()
        )
        if record is None or record.user_id != user_id:
            logger.info(
                "WebAuthn assertion for unknown/mismatched credential %s (user %s)",
                credential_id_b64, user_id,
            )
            raise WebAuthnAuthenticationError("Unknown or mismatched WebAuthn credential")

        try:
            verification = verify_authentication_response(
                credential=credential,
                expected_challenge=expected_challenge,
                expected_rp_id=self._rp_id,
                expected_origin=self._origin,
                credential_public_key=base64url_to_bytes(record.public_key),
                credential_current_sign_count=record.sign_count,
                require_user_verification=self._require_uv,
            )
        except InvalidAuthenticationResponse as exc:
            logger.info("WebAuthn authentication verification failed for user %s: %s", user_id, exc)
            raise WebAuthnAuthenticationError(str(exc)) from exc

        record.sign_count = verification.new_sign_count
        self._db.commit()
        logger.info("WebAuthn authentication succeeded for user %s", user_id)
        return AuthenticationResult(
            user_id=user_id,
            credential_id=record.credential_id,
            new_sign_count=verification.new_sign_count,
            user_verified=verification.user_verified,
        )

    # ── Internal helpers ─────────────────────────────────────────────────────

    def _user_credentials(self, user_id: UUID) -> List[WebAuthnCredential]:
        """Return all WebAuthn credentials registered to a user."""
        return (
            self._db.query(WebAuthnCredential)
            .filter(WebAuthnCredential.user_id == user_id)
            .all()
        )

    @staticmethod
    def _extract_credential_id(credential: CredentialInput) -> str:
        """
        Pull the base64url credential id out of an assertion response.

        Accepts the raw JSON string or a decoded dict (the two shapes the auth
        router forwards). Raises :class:`WebAuthnAuthenticationError` if the id
        cannot be found.
        """
        data: Any = credential
        if isinstance(credential, str):
            try:
                data = json.loads(credential)
            except (ValueError, TypeError) as exc:
                raise WebAuthnAuthenticationError("Malformed WebAuthn credential payload") from exc

        if isinstance(data, dict):
            cred_id = data.get("id") or data.get("rawId")
            if isinstance(cred_id, str) and cred_id:
                return cred_id

        # Fall back to a parsed py_webauthn struct exposing ``.id``.
        cred_id = getattr(data, "id", None)
        if isinstance(cred_id, str) and cred_id:
            return cred_id

        raise WebAuthnAuthenticationError("WebAuthn credential is missing an id")
