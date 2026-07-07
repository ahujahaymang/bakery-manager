"""
Security primitives for the app-first pivot Auth_Service.

This module concentrates all low-level cryptographic helpers used by the
Auth_Service so the higher-level logic (``auth_service.py``) never touches raw
hashing or token generation directly. It provides four groups of helpers:

1. **PIN hashing (argon2).** Per-user PINs are 4–8 digit secrets that live for
   the life of the user, so they are hashed with argon2id — a slow, memory-hard
   function that resists offline brute force if the registry DB is compromised
   (Requirement 4.1).

2. **OTP hashing (salted, short-lived).** One-time passcodes are low-entropy
   (4–8 digits) but short-lived (5-minute validity, 5-attempt cap). They are
   stored as a random-salted SHA-256 digest rather than argon2: the salt defeats
   precomputation/rainbow tables and cross-challenge correlation, while the short
   validity window and attempt cap bound online guessing. The stored value is
   self-describing (``salt$digest``) so verification needs no side channel
   (Requirement 2.5).

3. **Device session tokens.** A trusted device holds a long-lived (30–90 day)
   bearer token. We generate a high-entropy random token, return the *raw* token
   to the caller exactly once, and store only its SHA-256 hash. A registry-DB
   leak therefore never exposes a usable token (Requirement 2.5, 2.6).

4. **Constant-time comparison.** All secret comparisons route through
   ``hmac.compare_digest`` to avoid timing side channels.

Nothing in this module performs any I/O or touches the database; callers own
persistence.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError, VerificationError


# ── Configuration ────────────────────────────────────────────────────────────

# argon2id parameters. These are the argon2-cffi library defaults, which target
# roughly the OWASP-recommended cost for interactive login on server hardware.
# They are named explicitly so the cost can be tuned in one place.
_ARGON2_TIME_COST = 3          # iterations
_ARGON2_MEMORY_COST = 65536    # KiB (64 MiB)
_ARGON2_PARALLELISM = 4        # lanes

# Number of random bytes used for the OTP salt and the device session token.
# 16 bytes (128 bits) of salt makes precomputation infeasible; 32 bytes
# (256 bits) of token entropy makes the raw token unguessable.
_OTP_SALT_BYTES = 16
_DEVICE_TOKEN_BYTES = 32

# Separator between the salt and digest in a stored OTP hash. ``$`` never
# appears in url-safe base64, so the split is unambiguous.
_OTP_HASH_SEPARATOR = "$"

# A single shared argon2 hasher instance is safe to reuse across calls.
_password_hasher = PasswordHasher(
    time_cost=_ARGON2_TIME_COST,
    memory_cost=_ARGON2_MEMORY_COST,
    parallelism=_ARGON2_PARALLELISM,
)


# ── Constant-time comparison ─────────────────────────────────────────────────

def constant_time_compare(a: str | bytes, b: str | bytes) -> bool:
    """
    Compare two secrets in constant time to avoid leaking their contents via
    timing side channels.

    Accepts ``str`` or ``bytes``; strings are compared by their UTF-8 encoding.
    Returns ``False`` (never raises) if the inputs are of mismatched types.
    """
    if isinstance(a, str):
        a = a.encode("utf-8")
    if isinstance(b, str):
        b = b.encode("utf-8")
    if not isinstance(a, (bytes, bytearray)) or not isinstance(b, (bytes, bytearray)):
        return False
    return hmac.compare_digest(a, b)


# ── PIN hashing (argon2id) ───────────────────────────────────────────────────

def hash_pin(pin: str) -> str:
    """
    Hash a PIN with argon2id and return the encoded hash string.

    The returned string embeds the algorithm, parameters, and salt, so it is
    the only value that needs to be persisted (in ``User.pin_hash``). This
    function does not validate the PIN format — callers (AuthService.set_pin)
    enforce the 4–8 digit rule before hashing (Requirement 4.1, 4.2).
    """
    return _password_hasher.hash(pin)


def verify_pin(stored_hash: str, pin: str) -> bool:
    """
    Verify a candidate PIN against a stored argon2 hash.

    Returns ``True`` on match and ``False`` on mismatch or on any malformed /
    empty stored hash. argon2 performs the comparison in constant time
    internally.
    """
    if not stored_hash:
        return False
    try:
        return _password_hasher.verify(stored_hash, pin)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def pin_hash_needs_rehash(stored_hash: str) -> bool:
    """
    Report whether a stored PIN hash was produced with out-of-date argon2
    parameters and should be re-hashed on next successful verification.
    """
    if not stored_hash:
        return False
    try:
        return _password_hasher.check_needs_rehash(stored_hash)
    except InvalidHashError:
        return False


# ── OTP hashing (salted, short-lived) ────────────────────────────────────────

def _b64encode(raw: bytes) -> str:
    """URL-safe base64 without padding, for compact storage."""
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _b64decode(encoded: str) -> bytes:
    """Inverse of :func:`_b64encode`, restoring any stripped padding."""
    padding = "=" * (-len(encoded) % 4)
    return base64.urlsafe_b64decode(encoded + padding)


def _salted_otp_digest(code: str, salt: bytes) -> bytes:
    """Compute the SHA-256 digest of ``salt || code`` for a given OTP salt."""
    return hashlib.sha256(salt + code.encode("utf-8")).digest()


def hash_otp(code: str) -> str:
    """
    Hash a one-time passcode with a fresh random salt.

    Returns a self-describing ``"<salt>$<digest>"`` string (both url-safe
    base64) suitable for storing in ``OtpChallenge.code_hash``. A new salt is
    generated on every call, so identical codes for different challenges hash
    to different values (Requirement 2.5).
    """
    salt = secrets.token_bytes(_OTP_SALT_BYTES)
    digest = _salted_otp_digest(code, salt)
    return f"{_b64encode(salt)}{_OTP_HASH_SEPARATOR}{_b64encode(digest)}"


def verify_otp(stored_hash: str, code: str) -> bool:
    """
    Verify a candidate OTP against a stored salted hash.

    Recomputes the salted digest with the salt embedded in ``stored_hash`` and
    compares it to the stored digest in constant time. Returns ``False`` (never
    raises) on any malformed or empty stored hash.
    """
    if not stored_hash or _OTP_HASH_SEPARATOR not in stored_hash:
        return False
    salt_part, _, digest_part = stored_hash.partition(_OTP_HASH_SEPARATOR)
    try:
        salt = _b64decode(salt_part)
        expected_digest = _b64decode(digest_part)
    except (ValueError, base64.binascii.Error):
        return False
    candidate_digest = _salted_otp_digest(code, salt)
    return hmac.compare_digest(candidate_digest, expected_digest)


# ── Device session tokens ────────────────────────────────────────────────────

def generate_device_token() -> str:
    """
    Generate a high-entropy, url-safe device session token.

    The raw token is returned to the caller exactly once (handed to the client
    after successful OTP verification) and must never be persisted directly —
    store :func:`hash_token_for_storage` of it instead (Requirement 2.5, 2.6).
    """
    return secrets.token_urlsafe(_DEVICE_TOKEN_BYTES)


def hash_token_for_storage(token: str) -> str:
    """
    Compute the SHA-256 hex digest of a device session token for storage in
    ``Device.token_hash``.

    A plain (unsalted) SHA-256 is appropriate here because the token itself is
    high-entropy random (256 bits), so precomputation/rainbow-table attacks are
    infeasible and a per-token salt would add nothing. Lookups by ``token_hash``
    stay a simple indexed equality match.
    """
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def verify_token(stored_hash: str, token: str) -> bool:
    """
    Verify a presented device session token against its stored SHA-256 hash in
    constant time. Returns ``False`` on an empty stored hash.
    """
    if not stored_hash:
        return False
    return constant_time_compare(hash_token_for_storage(token), stored_hash)
