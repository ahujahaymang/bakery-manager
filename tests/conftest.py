"""
Shared pytest fixtures for all tests.

Provides an in-memory SQLite database with the full schema,
and a pre-created tenant for use in service tests.
"""

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker, Session
from uuid import UUID

from app.models import Base, Tenant


@pytest.fixture(scope="function")
def db() -> Session:
    """
    Provide a fresh in-memory SQLite database for each test.
    All tables are created from the ORM models and dropped after the test.
    """
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
    )

    @event.listens_for(engine, "connect")
    def set_pragmas(conn, _):
        conn.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine)
    session = SessionLocal()

    yield session

    session.close()
    Base.metadata.drop_all(engine)
    engine.dispose()


@pytest.fixture(scope="function")
def tenant(db: Session) -> Tenant:
    """Create and return a test tenant."""
    t = Tenant(chat_id="test_chat_001")
    db.add(t)
    db.commit()
    db.refresh(t)
    return t


@pytest.fixture(scope="function")
def tenant_id(tenant: Tenant) -> UUID:
    """Return the UUID of the test tenant."""
    return tenant.tenant_id
