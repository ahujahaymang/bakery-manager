"""
Database engine and session management.

Supports two modes controlled by DB_ENGINE:

  sqlite      — one SQLite file per tenant, stored at SQLITE_PATH/<tenant_id>.db
                A shared registry file (tenants.db) maps chat_id → tenant_id.
                Engines are cached in-process and reused across requests.
                Schema is auto-created on first access for each tenant.

  postgresql  — single shared PostgreSQL database, tenant isolation via tenant_id column.
                One global engine with connection pooling.
"""

import logging
import threading
from pathlib import Path
from typing import Dict, Generator, List, Optional
from uuid import UUID

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, Session

from app.config import settings

logger = logging.getLogger(__name__)

Base = declarative_base()


# ── PostgreSQL (single global engine) ─────────────────────────────────────────

def _build_postgres_engine() -> Engine:
    return create_engine(
        settings.DATABASE_URL,
        pool_pre_ping=True,
        pool_size=10,
        max_overflow=20,
        pool_recycle=3600,
        echo=False,
    )


# ── SQLite helpers ─────────────────────────────────────────────────────────────

def _build_sqlite_engine(db_path: str) -> Engine:
    """Create a SQLite engine for a single database file."""
    engine = create_engine(
        f"sqlite:///{db_path}",
        connect_args={"check_same_thread": False},
        echo=False,
    )

    @event.listens_for(engine, "connect")
    def set_pragmas(conn, _):
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")

    # Import models here to ensure they're registered on Base before create_all.
    # This avoids circular import issues (models import Base from this module).
    import app.models  # noqa: F401
    Base.metadata.create_all(engine)
    return engine


# ── Per-tenant SQLite registry ─────────────────────────────────────────────────

class _TenantEngineRegistry:
    """
    Thread-safe cache of SQLite engines, one per tenant.

    Layout on disk:
        <SQLITE_PATH>/
            tenants.db          ← shared registry: chat_id → tenant_id
            <tenant_id>.db      ← per-tenant business data
            <tenant_id>.db
            ...

    Engines are created on first access and reused for the lifetime of the
    process. Each tenant's data is fully isolated in its own file.
    """

    def __init__(self, data_dir: str):
        self._dir = Path(data_dir)
        self._dir.mkdir(parents=True, exist_ok=True)
        self._engines: Dict[str, Engine] = {}
        self._lock = threading.Lock()

        # Shared registry engine (tenants table only)
        registry_path = str(self._dir / "tenants.db")
        self._registry_engine = _build_sqlite_engine(registry_path)

    @property
    def registry_engine(self) -> Engine:
        """Engine for the shared tenants registry database."""
        return self._registry_engine

    def get(self, tenant_id: UUID) -> Engine:
        """Return the business-data engine for this tenant, creating it if needed."""
        key = str(tenant_id)
        if key not in self._engines:
            with self._lock:
                if key not in self._engines:
                    db_path = self._dir / f"{key}.db"
                    logger.info(f"Opening SQLite database for tenant {key}: {db_path}")
                    engine = _build_sqlite_engine(str(db_path))
                    self._engines[key] = engine
                    # Seed the tenant row so FK constraints pass in the business DB
                    self._seed_tenant_row(engine, tenant_id)
        return self._engines[key]

    def _seed_tenant_row(self, engine: Engine, tenant_id: UUID) -> None:
        """
        Ensure the tenant's own row exists in the business database.

        The business DB has a tenants table (created by Base.metadata.create_all)
        but it starts empty. FK constraints on customers, orders, etc. require
        the tenant row to exist.

        On first access of a brand-new DB: insert a minimal placeholder row.
        On re-open of an existing DB: do nothing — never overwrite real data.
        """
        from sqlalchemy.orm import sessionmaker
        from datetime import datetime
        import sqlalchemy as sa

        SessionLocal = sessionmaker(bind=engine)
        session = SessionLocal()
        try:
            result = session.execute(
                sa.text("SELECT COUNT(*) FROM tenants WHERE tenant_id = :tid"),
                {"tid": str(tenant_id)}
            ).scalar()
            if result == 0:
                # Brand-new DB — insert a minimal placeholder so FK constraints pass.
                # TenantService will populate business_name, chat_id, etc. during onboarding.
                session.execute(
                    sa.text(
                        "INSERT INTO tenants (tenant_id, chat_id, subscription_status, messaging_platform, created_at, updated_at) "
                        "VALUES (:tid, :cid, 'pending', 'telegram', :now, :now)"
                    ),
                    {"tid": str(tenant_id), "cid": str(tenant_id), "now": datetime.utcnow()}
                )
                session.commit()
                logger.info(f"Seeded tenant row in business DB for {tenant_id}")
            # If row already exists, never touch it — real data must not be overwritten.
        except Exception as e:
            session.rollback()
            logger.warning(f"Could not seed tenant row: {e}")
        finally:
            session.close()

    def db_path(self, tenant_id: UUID) -> str:
        """Return the filesystem path to a tenant's database file."""
        return str(self._dir / f"{str(tenant_id)}.db")

    def all_db_paths(self) -> List[str]:
        """Return paths to all existing tenant database files (excludes tenants.db)."""
        return [str(p) for p in self._dir.glob("*.db") if p.name != "tenants.db"]


# ── Module-level singletons ────────────────────────────────────────────────────

if settings.DB_ENGINE == "sqlite":
    _registry = _TenantEngineRegistry(settings.SQLITE_PATH)
    _pg_engine: Optional[Engine] = None
else:
    _registry = None
    _pg_engine = _build_postgres_engine()


# ── Public API ─────────────────────────────────────────────────────────────────

def get_registry_db() -> Generator[Session, None, None]:
    """
    Yield a session connected to the shared tenant registry database.

    SQLite mode: connects to tenants.db (chat_id → tenant_id mapping)
    PostgreSQL mode: connects to the shared database (same as get_db)

    Use this only for TenantService operations.
    """
    if settings.DB_ENGINE == "sqlite":
        engine = _registry.registry_engine
    else:
        engine = _pg_engine

    factory = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    db = factory()
    try:
        yield db
    finally:
        db.close()


def get_db(tenant_id: Optional[UUID] = None) -> Generator[Session, None, None]:
    """
    Yield a session connected to the tenant's business database.

    SQLite mode: opens <SQLITE_PATH>/<tenant_id>.db
    PostgreSQL mode: opens the shared database (tenant_id ignored for connection,
                     but all queries must still filter by tenant_id)

    Args:
        tenant_id: Required for SQLite mode. Ignored for PostgreSQL.
    """
    if settings.DB_ENGINE == "sqlite":
        if tenant_id is None:
            raise ValueError("tenant_id is required for SQLite per-tenant mode")
        engine = _registry.get(tenant_id)
    else:
        engine = _pg_engine

    factory = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    db = factory()
    try:
        yield db
    finally:
        db.close()


def get_tenant_db_path(tenant_id: UUID) -> Optional[str]:
    """Return the filesystem path to a tenant's SQLite database, or None for PostgreSQL."""
    if settings.DB_ENGINE == "sqlite" and _registry:
        return _registry.db_path(tenant_id)
    return None


def get_all_tenant_db_paths() -> List[str]:
    """Return paths to all tenant SQLite database files. Empty list for PostgreSQL."""
    if settings.DB_ENGINE == "sqlite" and _registry:
        return _registry.all_db_paths()
    return []
