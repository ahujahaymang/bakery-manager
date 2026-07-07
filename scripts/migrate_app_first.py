"""
App-First Pivot migration runner (Phase 0).

Applies the two additive schema surfaces introduced by the App-First Pivot,
matching the design's "Migration plan (Alembic + per-tenant SQLite reality)":

  1. Registry-DB new tables — ``users``, ``devices``, ``otp_challenges``,
     ``webauthn_credentials`` (defined in ``app.auth.models_auth``). These are
     registered on the shared ``Base`` and created idempotently via
     ``Base.metadata.create_all`` on the registry engine.

  2. Per-tenant business-DB changes:
       - new table ``sell_idempotency`` — created automatically by
         ``create_all`` on each tenant file.
       - new column ``orders.created_by_user_id`` — ``create_all`` does NOT
         ALTER existing tables, so we sweep every tenant SQLite file returned
         by ``get_all_tenant_db_paths()`` and run a guarded
         ``ALTER TABLE orders ADD COLUMN created_by_user_id VARCHAR(36)``.
         The guard (a ``PRAGMA table_info`` existence check) makes the sweep
         idempotent and safe to re-run.

For PostgreSQL deployments, the per-file SQLite sweep does not apply; schema
changes are delegated to ``alembic upgrade head``. ``create_all`` is still run
on the shared engine as a safety net for the new registry tables.

Historical orders keep ``created_by_user_id = NULL`` (pre-pivot sales have no
attributed app user); the column is nullable precisely to accommodate this.

Usage::

    python -m scripts.migrate_app_first
    python scripts/migrate_app_first.py
"""

import logging
import subprocess
import sys
from pathlib import Path

# Ensure the project root is importable when run as ``python scripts/...``.
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from sqlalchemy import create_engine, text

# Importing these registers every table (business + auth) on ``Base.metadata``
# BEFORE any ``create_all`` call, so the new tables are known to the metadata.
import app.models  # noqa: F401  (registers business models + SellIdempotency)
import app.auth.models_auth  # noqa: F401  (registers User/Device/OtpChallenge/WebAuthnCredential)

from app.config import settings
from app.database import Base, open_registry_db, get_all_tenant_db_paths

logger = logging.getLogger("migrate_app_first")

# Matches PortableUUID's SQLite storage (String(36)).
_ADD_COLUMN_SQL = "ALTER TABLE orders ADD COLUMN created_by_user_id VARCHAR(36)"


def _create_all_on_registry() -> None:
    """Idempotently create the new registry-DB auth tables on the shared engine."""
    with open_registry_db() as db:
        engine = db.get_bind()
        logger.info("Running create_all on the registry engine (%s)", engine.url)
        Base.metadata.create_all(engine)
    logger.info("Registry tables ensured (users, devices, otp_challenges, webauthn_credentials).")


def _orders_table_exists(conn) -> bool:
    """Return True if the ``orders`` table exists in this SQLite file."""
    row = conn.execute(
        text("SELECT name FROM sqlite_master WHERE type='table' AND name='orders'")
    ).fetchone()
    return row is not None


def _orders_has_created_by_column(conn) -> bool:
    """Return True if ``orders.created_by_user_id`` already exists."""
    rows = conn.execute(text("PRAGMA table_info(orders)")).fetchall()
    # PRAGMA table_info columns: (cid, name, type, notnull, dflt_value, pk)
    return any(r[1] == "created_by_user_id" for r in rows)


def _sweep_tenant_file(db_path: str) -> str:
    """
    Apply the additive migration to a single tenant SQLite file.

    Returns one of: "added", "already_present", "no_orders_table".
    """
    engine = create_engine(f"sqlite:///{db_path}")
    try:
        # create_all ensures the new sell_idempotency table exists on this file.
        # (It is a no-op for tables that already exist.)
        Base.metadata.create_all(engine)

        with engine.begin() as conn:
            if not _orders_table_exists(conn):
                logger.warning("  %s: no 'orders' table — skipping ALTER", db_path)
                return "no_orders_table"

            if _orders_has_created_by_column(conn):
                logger.info("  %s: orders.created_by_user_id already present", db_path)
                return "already_present"

            conn.execute(text(_ADD_COLUMN_SQL))
            logger.info("  %s: + orders.created_by_user_id", db_path)
            return "added"
    finally:
        engine.dispose()


def _run_sqlite_sweep() -> dict:
    """Sweep every tenant SQLite file, adding the attribution column where absent."""
    paths = get_all_tenant_db_paths()
    logger.info("Found %d tenant SQLite file(s) to sweep.", len(paths))

    counts = {"added": 0, "already_present": 0, "no_orders_table": 0}
    for db_path in paths:
        result = _sweep_tenant_file(db_path)
        counts[result] += 1
    counts["total"] = len(paths)
    return counts


def _run_alembic_upgrade() -> None:
    """Delegate Postgres schema changes to Alembic."""
    logger.info("PostgreSQL detected — delegating schema changes to 'alembic upgrade head'.")
    subprocess.run(["alembic", "upgrade", "head"], check=True, cwd=str(_PROJECT_ROOT))
    logger.info("Alembic upgrade complete.")


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    logger.info("App-First Pivot migration starting (DB_ENGINE=%s).", settings.DB_ENGINE)

    # Surface 1: new registry auth tables (both engine modes).
    _create_all_on_registry()

    # Surface 2: per-tenant business-DB changes.
    if settings.DB_ENGINE == "sqlite":
        counts = _run_sqlite_sweep()
        print("\n=== App-First migration summary (SQLite) ===")
        print(f"  tenant files swept   : {counts['total']}")
        print(f"  column added         : {counts['added']}")
        print(f"  already present      : {counts['already_present']}")
        print(f"  no orders table      : {counts['no_orders_table']}")
        print("  registry auth tables : ensured")
        print("============================================")
    else:
        _run_alembic_upgrade()
        print("\n=== App-First migration summary (PostgreSQL) ===")
        print("  registry auth tables : ensured (create_all safety net)")
        print("  schema changes       : applied via 'alembic upgrade head'")
        print("================================================")

    logger.info("App-First Pivot migration finished.")


if __name__ == "__main__":
    main()
