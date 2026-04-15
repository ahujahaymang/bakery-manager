"""
Tests for TenantService.

Tests tenant creation, resolution, and validation logic.
"""

import pytest
from uuid import UUID
from sqlalchemy import create_engine, Column, String, DateTime
from sqlalchemy.orm import sessionmaker, Session
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.dialects.postgresql import UUID as PGUUID
import uuid
from datetime import datetime

from app.services.tenant_service import TenantService


# Use in-memory SQLite for testing
TEST_DATABASE_URL = "sqlite:///:memory:"

# Create a test-specific Base and Tenant model for SQLite compatibility
TestBase = declarative_base()


class TestTenant(TestBase):
    """Test version of Tenant model compatible with SQLite."""
    __tablename__ = "tenants"
    
    tenant_id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    chat_id = Column(String, unique=True, nullable=False, index=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, nullable=False)


@pytest.fixture
def db_session(monkeypatch):
    """Create a test database session."""
    engine = create_engine(TEST_DATABASE_URL, connect_args={"check_same_thread": False})
    TestBase.metadata.create_all(bind=engine)
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    
    # Monkey patch the Tenant model to use TestTenant for testing
    import app.models
    original_tenant = app.models.Tenant
    monkeypatch.setattr(app.models, "Tenant", TestTenant)
    
    session = TestingSessionLocal()
    try:
        yield session
    finally:
        session.close()
        TestBase.metadata.drop_all(bind=engine)
        monkeypatch.setattr(app.models, "Tenant", original_tenant)


@pytest.fixture
def tenant_service(db_session):
    """Create a TenantService instance with test database."""
    return TenantService(db_session)


class TestTenantService:
    """Test suite for TenantService."""
    
    def test_get_or_create_tenant_creates_new_tenant(self, tenant_service, db_session):
        """Test that get_or_create_tenant creates a new tenant for unknown chat_id."""
        chat_id = "test_chat_123"
        
        # Verify tenant doesn't exist
        assert tenant_service.get_tenant_by_chat_id(chat_id) is None
        
        # Create tenant
        tenant = tenant_service.get_or_create_tenant(chat_id)
        
        # Verify tenant was created
        assert tenant is not None
        assert tenant.tenant_id is not None  # UUID stored as string in SQLite
        assert tenant.chat_id == chat_id
        assert tenant.created_at is not None
        assert tenant.updated_at is not None
    
    def test_get_or_create_tenant_returns_existing_tenant(self, tenant_service, db_session):
        """Test that get_or_create_tenant returns existing tenant for known chat_id."""
        chat_id = "test_chat_456"
        
        # Create tenant first time
        tenant1 = tenant_service.get_or_create_tenant(chat_id)
        tenant1_id = tenant1.tenant_id
        
        # Get tenant second time
        tenant2 = tenant_service.get_or_create_tenant(chat_id)
        
        # Verify same tenant is returned
        assert tenant2.tenant_id == tenant1_id
        assert tenant2.chat_id == chat_id
    
    def test_get_tenant_by_chat_id_returns_none_for_unknown(self, tenant_service):
        """Test that get_tenant_by_chat_id returns None for unknown chat_id."""
        result = tenant_service.get_tenant_by_chat_id("unknown_chat")
        assert result is None
    
    def test_get_tenant_by_chat_id_returns_tenant_for_known(self, tenant_service, db_session):
        """Test that get_tenant_by_chat_id returns tenant for known chat_id."""
        chat_id = "test_chat_789"
        
        # Create tenant
        created_tenant = tenant_service.get_or_create_tenant(chat_id)
        
        # Retrieve tenant
        retrieved_tenant = tenant_service.get_tenant_by_chat_id(chat_id)
        
        # Verify correct tenant is returned
        assert retrieved_tenant is not None
        assert retrieved_tenant.tenant_id == created_tenant.tenant_id
        assert retrieved_tenant.chat_id == chat_id
    
    def test_get_tenant_by_id_returns_tenant(self, tenant_service, db_session):
        """Test that get_tenant_by_id returns tenant for valid tenant_id."""
        chat_id = "test_chat_101"
        
        # Create tenant
        created_tenant = tenant_service.get_or_create_tenant(chat_id)
        
        # Retrieve by ID
        retrieved_tenant = tenant_service.get_tenant_by_id(created_tenant.tenant_id)
        
        # Verify correct tenant is returned
        assert retrieved_tenant is not None
        assert retrieved_tenant.tenant_id == created_tenant.tenant_id
        assert retrieved_tenant.chat_id == chat_id
    
    def test_get_tenant_by_id_returns_none_for_unknown(self, tenant_service):
        """Test that get_tenant_by_id returns None for unknown tenant_id."""
        from uuid import uuid4
        unknown_id = uuid4()
        
        result = tenant_service.get_tenant_by_id(unknown_id)
        assert result is None
    
    def test_get_or_create_tenant_validates_empty_chat_id(self, tenant_service):
        """Test that get_or_create_tenant raises ValueError for empty chat_id."""
        with pytest.raises(ValueError, match="chat_id cannot be empty"):
            tenant_service.get_or_create_tenant("")
        
        with pytest.raises(ValueError, match="chat_id cannot be empty"):
            tenant_service.get_or_create_tenant("   ")
    
    def test_get_tenant_by_chat_id_validates_empty_chat_id(self, tenant_service):
        """Test that get_tenant_by_chat_id raises ValueError for empty chat_id."""
        with pytest.raises(ValueError, match="chat_id cannot be empty"):
            tenant_service.get_tenant_by_chat_id("")
        
        with pytest.raises(ValueError, match="chat_id cannot be empty"):
            tenant_service.get_tenant_by_chat_id("   ")
    
    def test_get_or_create_tenant_strips_whitespace(self, tenant_service):
        """Test that get_or_create_tenant strips whitespace from chat_id."""
        chat_id_with_spaces = "  test_chat_202  "
        chat_id_clean = "test_chat_202"
        
        # Create with spaces
        tenant1 = tenant_service.get_or_create_tenant(chat_id_with_spaces)
        
        # Get without spaces
        tenant2 = tenant_service.get_tenant_by_chat_id(chat_id_clean)
        
        # Verify same tenant
        assert tenant2 is not None
        assert tenant1.tenant_id == tenant2.tenant_id
        assert tenant1.chat_id == chat_id_clean
