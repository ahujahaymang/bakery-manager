"""
App-First Pivot data seed (Phase 0) — Owner users for existing tenants.

Before the pivot, a "tenant" was just a chat (Telegram/WhatsApp) with no notion
of an authenticated app *user*. The App-First model requires every tenant to
have at least one Owner ``User`` so that phone+OTP login can resolve
``phone → tenant → user`` (see design "New tables — live in the registry DB").

This script backfills that: it iterates every existing tenant in the registry
DB and creates **exactly one** Owner-role ``User`` per tenant that does not
already have one, binding the new user to its ``tenant_id`` (Req 5.1, 5.5).

Phone selection (best-effort, from the tenant's chat context):
  - WhatsApp tenants store the owner's phone number as ``chat_id``. When that
    value is a valid phone, it is used directly (normalized to the E.164-ish
    shape stored on ``User.phone``).
  - Telegram tenants store a numeric Telegram *chat id* (not a phone), so no
    real phone is available. These fall back to a deterministic placeholder
    derived from the ``tenant_id`` (``_placeholder_phone``). The owner can
    correct their phone on first login/OTP.
  - Any tenant whose ``chat_id`` is missing or not a valid phone also falls
    back to the placeholder.

Idempotency:
  - Re-running does not create duplicate Owners. A tenant that already has an
    Owner ``User`` is skipped. The placeholder is deterministic per tenant, so
    the ``UniqueConstraint(tenant_id, phone)`` on ``users`` is also respected
    on re-run.

Run this AFTER ``scripts/migrate_app_first.py`` (which creates the ``users``
table). Usage::

    python -m scripts.seed_owner_users
    python scripts/seed_owner_users.py
    python scripts/seed_owner_users.py --dry-run
"""

import argparse
import logging
import re
import sys
import uuid
from datetime import datetime
from pathlib import Path

# Ensure the project root is importable when run as ``python scripts/...``.
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

# Importing these registers every table (business + auth) on ``Base.metadata``
# so the registry engine knows about ``users`` before we query/insert.
import app.models  # noqa: F401,E402
import app.auth.models_auth  # noqa: F401,E402

from app.models import Tenant  # noqa: E402
from app.auth.models_auth import User  # noqa: E402
from app.database import open_registry_db  # noqa: E402

logger = logging.getLogger("seed_owner_users")

# Mirror the phone shape used by AuthService: optional leading '+', 8–15 digits.
_PHONE_RE = re.compile(r"^\+?\d{8,15}$")
_PHONE_STRIP_RE = re.compile(r"[\s\-().]")

_DEFAULT_OWNER_NAME = "Owner"


def _normalize_phone(value: str) -> str:
    """Strip spaces and common separators, preserving a leading ``+``."""
    if not value:
        return ""
    return _PHONE_STRIP_RE.sub("", value.strip())


def _is_valid_phone(normalized: str) -> bool:
    """Return whether ``normalized`` matches the accepted phone shape."""
    return bool(_PHONE_RE.match(normalized))


def _placeholder_phone(tenant_id) -> str:
    """
    Deterministic placeholder phone for a tenant with no usable chat phone.

    Derived from the tenant UUID so that re-running the seed always produces the
    same value (respecting ``UniqueConstraint(tenant_id, phone)``). Clearly
    marked as a seed placeholder so it is never mistaken for a real number; the
    owner replaces it on first OTP login.
    """
    return f"seed-owner-{tenant_id}"


def _phone_from_chat_context(tenant: Tenant) -> tuple[str, bool]:
    """
    Best-effort phone for a tenant from its chat context.

    Returns ``(phone, is_placeholder)``. Only WhatsApp tenants carry a real
    phone in ``chat_id``; everything else falls back to a placeholder.
    """
    platform = (tenant.messaging_platform or "").lower()
    if platform == "whatsapp":
        normalized = _normalize_phone(tenant.chat_id or "")
        if _is_valid_phone(normalized):
            return normalized, False

    return _placeholder_phone(tenant.tenant_id), True


def _owner_name(tenant: Tenant) -> str:
    """Owner display name — the business name when set, else a default."""
    name = (tenant.business_name or "").strip()
    return name if name else _DEFAULT_OWNER_NAME


def seed_owner_users(dry_run: bool = False) -> dict:
    """
    Create one Owner ``User`` per existing tenant that lacks one.

    Args:
        dry_run: When True, log what would happen but persist nothing.

    Returns:
        A summary dict with counts:
          total_tenants           — tenants scanned
          created                 — new Owner users created
          skipped_existing_owner  — tenants that already had an Owner
          placeholder_phone       — created owners that used a placeholder phone
          chat_phone              — created owners that used a real chat phone
    """
    counts = {
        "total_tenants": 0,
        "created": 0,
        "skipped_existing_owner": 0,
        "placeholder_phone": 0,
        "chat_phone": 0,
    }

    with open_registry_db() as db:
        tenants = db.query(Tenant).all()
        counts["total_tenants"] = len(tenants)
        logger.info("Scanning %d tenant(s) for Owner backfill.", len(tenants))

        # Tenants that already have at least one Owner — idempotency guard.
        existing_owner_tenant_ids = {
            row[0]
            for row in db.query(User.tenant_id).filter(User.role == "owner").all()
        }

        to_create = []
        for tenant in tenants:
            if tenant.tenant_id in existing_owner_tenant_ids:
                counts["skipped_existing_owner"] += 1
                logger.info("  %s: already has an Owner — skipping", tenant.tenant_id)
                continue

            phone, is_placeholder = _phone_from_chat_context(tenant)
            name = _owner_name(tenant)

            if is_placeholder:
                counts["placeholder_phone"] += 1
                logger.warning(
                    "  %s: no usable chat phone (platform=%s, chat_id=%r) — using placeholder %r",
                    tenant.tenant_id, tenant.messaging_platform, tenant.chat_id, phone,
                )
            else:
                counts["chat_phone"] += 1
                logger.info("  %s: using chat phone %r", tenant.tenant_id, phone)

            to_create.append(
                User(
                    user_id=uuid.uuid4(),
                    tenant_id=tenant.tenant_id,
                    name=name,
                    phone=phone,
                    role="owner",
                    created_at=datetime.utcnow(),
                )
            )

        counts["created"] = len(to_create)

        if dry_run:
            logger.info("[dry-run] would create %d Owner user(s); no changes persisted.", len(to_create))
            return counts

        for user in to_create:
            db.add(user)
        db.commit()
        logger.info("Created %d Owner user(s).", len(to_create))

    return counts


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    parser = argparse.ArgumentParser(description="Seed Owner users for existing tenants.")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report what would be created without persisting any changes.",
    )
    args = parser.parse_args()

    logger.info("Owner-user seed starting%s.", " (dry-run)" if args.dry_run else "")
    counts = seed_owner_users(dry_run=args.dry_run)

    print("\n=== Owner-user seed summary ===")
    print(f"  tenants scanned        : {counts['total_tenants']}")
    print(f"  owners created         : {counts['created']}")
    print(f"    - from chat phone    : {counts['chat_phone']}")
    print(f"    - from placeholder   : {counts['placeholder_phone']}")
    print(f"  skipped (had owner)    : {counts['skipped_existing_owner']}")
    print("===============================")

    logger.info("Owner-user seed finished.")


if __name__ == "__main__":
    main()
