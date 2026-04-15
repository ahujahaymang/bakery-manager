"""
Tests for CustomerService.

Tests customer creation, search, retrieval, and validation logic.
"""

import pytest
from uuid import uuid4
from sqlalchemy import create_engine, Column, String, DateTime
from sqlalchemy.orm import sessionmaker
from sqlalchemy.ext.declarative import declarative_base
from datetime import datetime

from app.services.customer_service import CustomerService


# Use in-memory SQLite for testing
TEST_DATABASE_URL = "sqlite:///:memory:"

# Create a test-specific Base and Customer model for SQLite compatibility
TestBase = declarative_base()


class TestCustomer(TestBase):
    """Test version of Customer model compatible with SQLite."""
    __tablename__ = "customers"
    
    customer_id = Column(String, primary_key=True, default=lambda: str(uuid4()))
    tenant_id = Column(String, nullable=False, index=True)
    name = Column(String, nullable=False)
    phone = Column(String, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, nullable=False)


@pytest.fixture
def db_session(monkeypatch):
    """Create a test database session."""
    engine = create_engine(TEST_DATABASE_URL, connect_args={"check_same_thread": False})
    TestBase.metadata.create_all(bind=engine)
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    
    # Monkey patch the Customer model to use TestCustomer for testing
    import app.models
    original_customer = app.models.Customer
    monkeypatch.setattr(app.models, "Customer", TestCustomer)
    
    session = TestingSessionLocal()
    try:
        yield session
    finally:
        session.close()
        TestBase.metadata.drop_all(bind=engine)
        monkeypatch.setattr(app.models, "Customer", original_customer)


@pytest.fixture
def customer_service(db_session):
    """Create a CustomerService instance with test database."""
    return CustomerService(db_session)


@pytest.fixture
def tenant_id():
    """Generate a test tenant ID."""
    return uuid4()


class TestCustomerService:
    """Test suite for CustomerService."""
    
    def test_create_customer_with_valid_data(self, customer_service, tenant_id):
        """Test that create_customer creates a customer with valid data."""
        name = "John Doe"
        phone = "1234567890"
        
        customer = customer_service.create_customer(tenant_id, name, phone)
        
        assert customer is not None
        assert customer.customer_id is not None
        assert customer.tenant_id == tenant_id
        assert customer.name == name
        assert customer.phone == phone
        assert customer.created_at is not None
        assert customer.updated_at is not None
    
    def test_create_customer_validates_missing_name(self, customer_service, tenant_id):
        """Test that create_customer raises ValueError for missing name."""
        with pytest.raises(ValueError, match="Customer name is required"):
            customer_service.create_customer(tenant_id, "", "1234567890")
        
        with pytest.raises(ValueError, match="Customer name is required"):
            customer_service.create_customer(tenant_id, "   ", "1234567890")
    
    def test_create_customer_validates_missing_phone(self, customer_service, tenant_id):
        """Test that create_customer raises ValueError for missing phone."""
        with pytest.raises(ValueError, match="Customer phone number is required"):
            customer_service.create_customer(tenant_id, "John Doe", "")
        
        with pytest.raises(ValueError, match="Customer phone number is required"):
            customer_service.create_customer(tenant_id, "John Doe", "   ")
    
    def test_create_customer_rejects_duplicate_phone(self, customer_service, tenant_id):
        """Test that create_customer rejects duplicate phone numbers."""
        name1 = "John Doe"
        name2 = "Jane Smith"
        phone = "1234567890"
        
        # Create first customer
        customer_service.create_customer(tenant_id, name1, phone)
        
        # Try to create second customer with same phone
        with pytest.raises(ValueError, match="already exists"):
            customer_service.create_customer(tenant_id, name2, phone)
    
    def test_create_customer_allows_same_phone_different_tenant(self, customer_service):
        """Test that same phone number is allowed for different tenants."""
        tenant_id1 = uuid4()
        tenant_id2 = uuid4()
        name = "John Doe"
        phone = "1234567890"
        
        # Create customer in first tenant
        customer1 = customer_service.create_customer(tenant_id1, name, phone)
        
        # Create customer with same phone in second tenant
        customer2 = customer_service.create_customer(tenant_id2, name, phone)
        
        assert customer1.phone == customer2.phone
        assert customer1.tenant_id != customer2.tenant_id
    
    def test_create_customer_strips_whitespace(self, customer_service, tenant_id):
        """Test that create_customer strips whitespace from inputs."""
        name = "  John Doe  "
        phone = "  1234567890  "
        
        customer = customer_service.create_customer(tenant_id, name, phone)
        
        assert customer.name == "John Doe"
        assert customer.phone == "1234567890"
    
    def test_get_customer_by_phone_exact_match(self, customer_service, tenant_id):
        """Test that get_customer finds customer by exact phone match."""
        name = "John Doe"
        phone = "1234567890"
        
        # Create customer
        created = customer_service.create_customer(tenant_id, name, phone)
        
        # Search by phone
        results = customer_service.get_customer(tenant_id, phone)
        
        assert len(results) == 1
        assert results[0].customer_id == created.customer_id
        assert results[0].phone == phone
    
    def test_get_customer_by_name_partial_match(self, customer_service, tenant_id):
        """Test that get_customer finds customer by partial name match."""
        name = "John Doe"
        phone = "1234567890"
        
        # Create customer
        created = customer_service.create_customer(tenant_id, name, phone)
        
        # Search by partial name
        results = customer_service.get_customer(tenant_id, "John")
        
        assert len(results) == 1
        assert results[0].customer_id == created.customer_id
    
    def test_get_customer_case_insensitive(self, customer_service, tenant_id):
        """Test that get_customer is case-insensitive for name search."""
        name = "John Doe"
        phone = "1234567890"
        
        # Create customer
        created = customer_service.create_customer(tenant_id, name, phone)
        
        # Search with different case
        results = customer_service.get_customer(tenant_id, "john doe")
        
        assert len(results) == 1
        assert results[0].customer_id == created.customer_id
    
    def test_get_customer_returns_multiple_matches(self, customer_service, tenant_id):
        """Test that get_customer returns all matching customers."""
        # Create multiple customers with similar names
        customer_service.create_customer(tenant_id, "John Doe", "1111111111")
        customer_service.create_customer(tenant_id, "John Smith", "2222222222")
        customer_service.create_customer(tenant_id, "Johnny Walker", "3333333333")
        
        # Search by partial name
        results = customer_service.get_customer(tenant_id, "John")
        
        assert len(results) == 3
    
    def test_get_customer_returns_empty_for_no_match(self, customer_service, tenant_id):
        """Test that get_customer returns empty list when no match found."""
        results = customer_service.get_customer(tenant_id, "Nonexistent")
        
        assert len(results) == 0
    
    def test_get_customer_tenant_isolation(self, customer_service):
        """Test that get_customer only returns customers from the same tenant."""
        tenant_id1 = uuid4()
        tenant_id2 = uuid4()
        name = "John Doe"
        
        # Create customer in first tenant
        customer_service.create_customer(tenant_id1, name, "1111111111")
        
        # Create customer in second tenant
        customer_service.create_customer(tenant_id2, name, "2222222222")
        
        # Search in first tenant
        results1 = customer_service.get_customer(tenant_id1, name)
        assert len(results1) == 1
        assert results1[0].tenant_id == tenant_id1
        
        # Search in second tenant
        results2 = customer_service.get_customer(tenant_id2, name)
        assert len(results2) == 1
        assert results2[0].tenant_id == tenant_id2
    
    def test_list_customers_returns_all(self, customer_service, tenant_id):
        """Test that list_customers returns all customers for tenant."""
        # Create multiple customers
        customer_service.create_customer(tenant_id, "Alice", "1111111111")
        customer_service.create_customer(tenant_id, "Bob", "2222222222")
        customer_service.create_customer(tenant_id, "Charlie", "3333333333")
        
        # List all customers
        results = customer_service.list_customers(tenant_id)
        
        assert len(results) == 3
    
    def test_list_customers_sorted_by_name(self, customer_service, tenant_id):
        """Test that list_customers returns customers sorted by name."""
        # Create customers in random order
        customer_service.create_customer(tenant_id, "Charlie", "3333333333")
        customer_service.create_customer(tenant_id, "Alice", "1111111111")
        customer_service.create_customer(tenant_id, "Bob", "2222222222")
        
        # List all customers
        results = customer_service.list_customers(tenant_id)
        
        assert len(results) == 3
        assert results[0].name == "Alice"
        assert results[1].name == "Bob"
        assert results[2].name == "Charlie"
    
    def test_list_customers_tenant_isolation(self, customer_service):
        """Test that list_customers only returns customers from the same tenant."""
        tenant_id1 = uuid4()
        tenant_id2 = uuid4()
        
        # Create customers in first tenant
        customer_service.create_customer(tenant_id1, "Alice", "1111111111")
        customer_service.create_customer(tenant_id1, "Bob", "2222222222")
        
        # Create customer in second tenant
        customer_service.create_customer(tenant_id2, "Charlie", "3333333333")
        
        # List customers in first tenant
        results1 = customer_service.list_customers(tenant_id1)
        assert len(results1) == 2
        
        # List customers in second tenant
        results2 = customer_service.list_customers(tenant_id2)
        assert len(results2) == 1
    
    def test_list_customers_returns_empty_for_no_customers(self, customer_service, tenant_id):
        """Test that list_customers returns empty list when no customers exist."""
        results = customer_service.list_customers(tenant_id)
        
        assert len(results) == 0
    
    def test_get_customer_by_id(self, customer_service, tenant_id):
        """Test that get_customer_by_id retrieves customer by ID."""
        name = "John Doe"
        phone = "1234567890"
        
        # Create customer
        created = customer_service.create_customer(tenant_id, name, phone)
        
        # Retrieve by ID
        retrieved = customer_service.get_customer_by_id(tenant_id, created.customer_id)
        
        assert retrieved is not None
        assert retrieved.customer_id == created.customer_id
        assert retrieved.name == name
        assert retrieved.phone == phone
    
    def test_get_customer_by_id_tenant_isolation(self, customer_service):
        """Test that get_customer_by_id enforces tenant isolation."""
        tenant_id1 = uuid4()
        tenant_id2 = uuid4()
        
        # Create customer in first tenant
        customer = customer_service.create_customer(tenant_id1, "John Doe", "1234567890")
        
        # Try to retrieve from second tenant
        result = customer_service.get_customer_by_id(tenant_id2, customer.customer_id)
        
        assert result is None
    
    def test_get_customer_by_phone(self, customer_service, tenant_id):
        """Test that get_customer_by_phone retrieves customer by phone."""
        name = "John Doe"
        phone = "1234567890"
        
        # Create customer
        created = customer_service.create_customer(tenant_id, name, phone)
        
        # Retrieve by phone
        retrieved = customer_service.get_customer_by_phone(tenant_id, phone)
        
        assert retrieved is not None
        assert retrieved.customer_id == created.customer_id
        assert retrieved.phone == phone
    
    def test_get_customer_by_phone_tenant_isolation(self, customer_service):
        """Test that get_customer_by_phone enforces tenant isolation."""
        tenant_id1 = uuid4()
        tenant_id2 = uuid4()
        phone = "1234567890"
        
        # Create customer in first tenant
        customer_service.create_customer(tenant_id1, "John Doe", phone)
        
        # Try to retrieve from second tenant
        result = customer_service.get_customer_by_phone(tenant_id2, phone)
        
        assert result is None
