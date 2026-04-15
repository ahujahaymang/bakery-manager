"""
Tests for OrderService.

This module contains unit tests for order creation, customer resolution,
and validation logic.
"""

import pytest
from uuid import uuid4
from decimal import Decimal
from datetime import date, timedelta, datetime
from sqlalchemy import create_engine, Column, String, Integer, Date, DateTime, ForeignKey, Numeric
from sqlalchemy.orm import sessionmaker, relationship
from sqlalchemy.ext.declarative import declarative_base

from app.services.order_service import OrderService, OrderCreate, OrderItemCreate


# Use in-memory SQLite for testing
TEST_DATABASE_URL = "sqlite:///:memory:"

# Create a test-specific Base for SQLite compatibility
TestBase = declarative_base()


class TestTenant(TestBase):
    """Test version of Tenant model compatible with SQLite."""
    __tablename__ = "tenants"
    
    tenant_id = Column(String, primary_key=True, default=lambda: str(uuid4()))
    chat_id = Column(String, unique=True, nullable=False, index=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    
    customers = relationship("TestCustomer", back_populates="tenant")
    recipes = relationship("TestRecipe", back_populates="tenant")
    orders = relationship("TestOrder", back_populates="tenant")


class TestCustomer(TestBase):
    """Test version of Customer model compatible with SQLite."""
    __tablename__ = "customers"
    
    customer_id = Column(String, primary_key=True, default=lambda: str(uuid4()))
    tenant_id = Column(String, ForeignKey("tenants.tenant_id"), nullable=False, index=True)
    name = Column(String, nullable=False)
    phone = Column(String, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    
    tenant = relationship("TestTenant", back_populates="customers")
    orders = relationship("TestOrder", back_populates="customer")


class TestRecipe(TestBase):
    """Test version of Recipe model compatible with SQLite."""
    __tablename__ = "recipes"
    
    recipe_id = Column(String, primary_key=True, default=lambda: str(uuid4()))
    tenant_id = Column(String, ForeignKey("tenants.tenant_id"), nullable=False, index=True)
    name = Column(String, nullable=False)
    yield_per_batch = Column(Integer, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    
    tenant = relationship("TestTenant", back_populates="recipes")
    order_items = relationship("TestOrderItem", back_populates="recipe")


class TestOrder(TestBase):
    """Test version of Order model compatible with SQLite."""
    __tablename__ = "orders"
    
    order_id = Column(String, primary_key=True, default=lambda: str(uuid4()))
    tenant_id = Column(String, ForeignKey("tenants.tenant_id"), nullable=False, index=True)
    customer_id = Column(String, ForeignKey("customers.customer_id"), nullable=False, index=True)
    delivery_date = Column(Date, nullable=False)
    status = Column(String, nullable=False, default="pending")
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    
    tenant = relationship("TestTenant", back_populates="orders")
    customer = relationship("TestCustomer", back_populates="orders")
    order_items = relationship("TestOrderItem", back_populates="order")


class TestOrderItem(TestBase):
    """Test version of OrderItem model compatible with SQLite."""
    __tablename__ = "order_items"
    
    order_item_id = Column(String, primary_key=True, default=lambda: str(uuid4()))
    order_id = Column(String, ForeignKey("orders.order_id"), nullable=False, index=True)
    recipe_id = Column(String, ForeignKey("recipes.recipe_id"), nullable=False, index=True)
    quantity = Column(Integer, nullable=False)
    selling_price = Column(Numeric(10, 2), nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    
    order = relationship("TestOrder", back_populates="order_items")
    recipe = relationship("TestRecipe", back_populates="order_items")


@pytest.fixture
def db_session(monkeypatch):
    """Create a test database session."""
    engine = create_engine(TEST_DATABASE_URL, connect_args={"check_same_thread": False})
    TestBase.metadata.create_all(bind=engine)
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    
    # Monkey patch the models to use test versions
    import app.models
    monkeypatch.setattr(app.models, "Tenant", TestTenant)
    monkeypatch.setattr(app.models, "Customer", TestCustomer)
    monkeypatch.setattr(app.models, "Recipe", TestRecipe)
    monkeypatch.setattr(app.models, "Order", TestOrder)
    monkeypatch.setattr(app.models, "OrderItem", TestOrderItem)
    
    session = TestingSessionLocal()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture
def tenant(db_session):
    """Create a test tenant."""
    tenant = TestTenant(chat_id="test_chat_123")
    db_session.add(tenant)
    db_session.commit()
    db_session.refresh(tenant)
    return tenant


@pytest.fixture
def customer(db_session, tenant):
    """Create a test customer."""
    customer = TestCustomer(
        tenant_id=tenant.tenant_id,
        name="John Doe",
        phone="1234567890"
    )
    db_session.add(customer)
    db_session.commit()
    db_session.refresh(customer)
    return customer


@pytest.fixture
def recipe(db_session, tenant):
    """Create a test recipe."""
    recipe = TestRecipe(
        tenant_id=tenant.tenant_id,
        name="Chocolate Cake",
        yield_per_batch=10
    )
    db_session.add(recipe)
    db_session.commit()
    db_session.refresh(recipe)
    return recipe


@pytest.fixture
def order_service(db_session):
    """Create an OrderService instance."""
    return OrderService(db_session)


class TestOrderCreation:
    """Tests for order creation functionality."""
    
    def test_create_order_with_valid_data(self, order_service, tenant, customer, recipe):
        """Test creating an order with valid data."""
        tomorrow = date.today() + timedelta(days=1)
        
        order_data = OrderCreate(
            customer_identifier=customer.phone,
            delivery_date=tomorrow,
            items=[
                OrderItemCreate(
                    recipe_name=recipe.name,
                    quantity=2,
                    selling_price=Decimal("25.00")
                )
            ]
        )
        
        order = order_service.create_order(tenant.tenant_id, order_data)
        
        assert order is not None
        assert order.customer_id == customer.customer_id
        assert order.delivery_date == tomorrow
        assert order.status == "pending"
        assert len(order.order_items) == 1
        assert order.order_items[0].quantity == 2
        assert order.order_items[0].selling_price == Decimal("25.00")
    
    def test_create_order_resolves_customer_by_name(self, order_service, tenant, customer, recipe):
        """Test that order creation resolves customer by name."""
        tomorrow = date.today() + timedelta(days=1)
        
        order_data = OrderCreate(
            customer_identifier=customer.name,
            delivery_date=tomorrow,
            items=[
                OrderItemCreate(
                    recipe_name=recipe.name,
                    quantity=1,
                    selling_price=Decimal("15.00")
                )
            ]
        )
        
        order = order_service.create_order(tenant.tenant_id, order_data)
        
        assert order.customer_id == customer.customer_id
    
    def test_create_order_with_multiple_items(self, order_service, db_session, tenant, customer, recipe):
        """Test creating an order with multiple items."""
        # Create another recipe
        recipe2 = TestRecipe(
            tenant_id=tenant.tenant_id,
            name="Vanilla Cake",
            yield_per_batch=8
        )
        db_session.add(recipe2)
        db_session.commit()
        
        tomorrow = date.today() + timedelta(days=1)
        
        order_data = OrderCreate(
            customer_identifier=customer.phone,
            delivery_date=tomorrow,
            items=[
                OrderItemCreate(
                    recipe_name=recipe.name,
                    quantity=2,
                    selling_price=Decimal("25.00")
                ),
                OrderItemCreate(
                    recipe_name=recipe2.name,
                    quantity=3,
                    selling_price=Decimal("20.00")
                )
            ]
        )
        
        order = order_service.create_order(tenant.tenant_id, order_data)
        
        assert len(order.order_items) == 2
        assert order.order_items[0].quantity == 2
        assert order.order_items[1].quantity == 3


class TestCustomerResolution:
    """Tests for customer resolution logic."""
    
    def test_customer_not_found(self, order_service, tenant, recipe):
        """Test error when customer is not found."""
        tomorrow = date.today() + timedelta(days=1)
        
        order_data = OrderCreate(
            customer_identifier="NonExistent Customer",
            delivery_date=tomorrow,
            items=[
                OrderItemCreate(
                    recipe_name=recipe.name,
                    quantity=1,
                    selling_price=Decimal("15.00")
                )
            ]
        )
        
        with pytest.raises(ValueError) as exc_info:
            order_service.create_order(tenant.tenant_id, order_data)
        
        assert "No customer found" in str(exc_info.value)
    
    def test_multiple_customers_disambiguation(self, order_service, db_session, tenant, recipe):
        """Test disambiguation when multiple customers match."""
        # Create two customers with similar names
        customer1 = TestCustomer(
            tenant_id=tenant.tenant_id,
            name="John Smith",
            phone="1111111111"
        )
        customer2 = TestCustomer(
            tenant_id=tenant.tenant_id,
            name="John Doe",
            phone="2222222222"
        )
        db_session.add_all([customer1, customer2])
        db_session.commit()
        
        tomorrow = date.today() + timedelta(days=1)
        
        order_data = OrderCreate(
            customer_identifier="John",  # Matches both customers
            delivery_date=tomorrow,
            items=[
                OrderItemCreate(
                    recipe_name=recipe.name,
                    quantity=1,
                    selling_price=Decimal("15.00")
                )
            ]
        )
        
        with pytest.raises(ValueError) as exc_info:
            order_service.create_order(tenant.tenant_id, order_data)
        
        error_message = str(exc_info.value)
        assert "Multiple customers match" in error_message
        assert "1111111111" in error_message
        assert "2222222222" in error_message


class TestValidation:
    """Tests for order validation logic."""
    
    def test_past_delivery_date_rejected(self, order_service, tenant, customer, recipe):
        """Test that past delivery dates are rejected."""
        yesterday = date.today() - timedelta(days=1)
        
        order_data = OrderCreate(
            customer_identifier=customer.phone,
            delivery_date=yesterday,
            items=[
                OrderItemCreate(
                    recipe_name=recipe.name,
                    quantity=1,
                    selling_price=Decimal("15.00")
                )
            ]
        )
        
        with pytest.raises(ValueError) as exc_info:
            order_service.create_order(tenant.tenant_id, order_data)
        
        assert "cannot be in the past" in str(exc_info.value)
    
    def test_negative_selling_price_rejected(self, order_service, tenant, customer, recipe):
        """Test that negative selling prices are rejected."""
        tomorrow = date.today() + timedelta(days=1)
        
        order_data = OrderCreate(
            customer_identifier=customer.phone,
            delivery_date=tomorrow,
            items=[
                OrderItemCreate(
                    recipe_name=recipe.name,
                    quantity=1,
                    selling_price=Decimal("-10.00")
                )
            ]
        )
        
        with pytest.raises(ValueError) as exc_info:
            order_service.create_order(tenant.tenant_id, order_data)
        
        assert "must be positive" in str(exc_info.value)
    
    def test_zero_selling_price_rejected(self, order_service, tenant, customer, recipe):
        """Test that zero selling prices are rejected."""
        tomorrow = date.today() + timedelta(days=1)
        
        order_data = OrderCreate(
            customer_identifier=customer.phone,
            delivery_date=tomorrow,
            items=[
                OrderItemCreate(
                    recipe_name=recipe.name,
                    quantity=1,
                    selling_price=Decimal("0.00")
                )
            ]
        )
        
        with pytest.raises(ValueError) as exc_info:
            order_service.create_order(tenant.tenant_id, order_data)
        
        assert "must be positive" in str(exc_info.value)
    
    def test_negative_quantity_rejected(self, order_service, tenant, customer, recipe):
        """Test that negative quantities are rejected."""
        tomorrow = date.today() + timedelta(days=1)
        
        order_data = OrderCreate(
            customer_identifier=customer.phone,
            delivery_date=tomorrow,
            items=[
                OrderItemCreate(
                    recipe_name=recipe.name,
                    quantity=-1,
                    selling_price=Decimal("15.00")
                )
            ]
        )
        
        with pytest.raises(ValueError) as exc_info:
            order_service.create_order(tenant.tenant_id, order_data)
        
        assert "must be a positive integer" in str(exc_info.value)
    
    def test_recipe_not_found(self, order_service, tenant, customer):
        """Test error when recipe is not found."""
        tomorrow = date.today() + timedelta(days=1)
        
        order_data = OrderCreate(
            customer_identifier=customer.phone,
            delivery_date=tomorrow,
            items=[
                OrderItemCreate(
                    recipe_name="NonExistent Recipe",
                    quantity=1,
                    selling_price=Decimal("15.00")
                )
            ]
        )
        
        with pytest.raises(ValueError) as exc_info:
            order_service.create_order(tenant.tenant_id, order_data)
        
        assert "Recipe" in str(exc_info.value)
        assert "not found" in str(exc_info.value)
    
    def test_missing_customer_identifier(self, order_service, tenant, recipe):
        """Test error when customer identifier is missing."""
        tomorrow = date.today() + timedelta(days=1)
        
        order_data = OrderCreate(
            customer_identifier="",
            delivery_date=tomorrow,
            items=[
                OrderItemCreate(
                    recipe_name=recipe.name,
                    quantity=1,
                    selling_price=Decimal("15.00")
                )
            ]
        )
        
        with pytest.raises(ValueError) as exc_info:
            order_service.create_order(tenant.tenant_id, order_data)
        
        assert "Customer identifier is required" in str(exc_info.value)
    
    def test_empty_items_list(self, order_service, tenant, customer):
        """Test error when items list is empty."""
        tomorrow = date.today() + timedelta(days=1)
        
        order_data = OrderCreate(
            customer_identifier=customer.phone,
            delivery_date=tomorrow,
            items=[]
        )
        
        with pytest.raises(ValueError) as exc_info:
            order_service.create_order(tenant.tenant_id, order_data)
        
        assert "At least one order item is required" in str(exc_info.value)


class TestTenantIsolation:
    """Tests for tenant isolation."""
    
    def test_customer_from_different_tenant_not_found(self, order_service, db_session, recipe):
        """Test that customers from other tenants are not accessible."""
        # Create two tenants
        tenant1 = TestTenant(chat_id="tenant1_chat")
        tenant2 = TestTenant(chat_id="tenant2_chat")
        db_session.add_all([tenant1, tenant2])
        db_session.commit()
        
        # Create customer for tenant1
        customer1 = TestCustomer(
            tenant_id=tenant1.tenant_id,
            name="Tenant1 Customer",
            phone="1111111111"
        )
        db_session.add(customer1)
        db_session.commit()
        
        # Create recipe for tenant2
        recipe2 = TestRecipe(
            tenant_id=tenant2.tenant_id,
            name="Tenant2 Recipe",
            yield_per_batch=5
        )
        db_session.add(recipe2)
        db_session.commit()
        
        tomorrow = date.today() + timedelta(days=1)
        
        # Try to create order for tenant2 using tenant1's customer
        order_data = OrderCreate(
            customer_identifier="Tenant1 Customer",
            delivery_date=tomorrow,
            items=[
                OrderItemCreate(
                    recipe_name=recipe2.name,
                    quantity=1,
                    selling_price=Decimal("15.00")
                )
            ]
        )
        
        with pytest.raises(ValueError) as exc_info:
            order_service.create_order(tenant2.tenant_id, order_data)
        
        assert "No customer found" in str(exc_info.value)


class TestMarkDelivered:
    """Tests for marking orders as delivered."""
    
    def test_mark_delivered_with_valid_order(self, order_service, tenant, customer, recipe):
        """Test marking an order as delivered with valid order ID."""
        # Create an order first
        tomorrow = date.today() + timedelta(days=1)
        order_data = OrderCreate(
            customer_identifier=customer.phone,
            delivery_date=tomorrow,
            items=[
                OrderItemCreate(
                    recipe_name=recipe.name,
                    quantity=2,
                    selling_price=Decimal("25.00")
                )
            ]
        )
        order = order_service.create_order(tenant.tenant_id, order_data)
        
        # Verify initial status
        assert order.status == "pending"
        
        # Mark as delivered
        updated_order = order_service.mark_delivered(tenant.tenant_id, order.order_id)
        
        # Verify status changed
        assert updated_order.status == "delivered"
        assert updated_order.order_id == order.order_id
        assert updated_order.updated_at is not None
    
    def test_mark_delivered_order_not_found(self, order_service, tenant):
        """Test error when order is not found."""
        fake_order_id = str(uuid4())
        
        with pytest.raises(ValueError) as exc_info:
            order_service.mark_delivered(tenant.tenant_id, fake_order_id)
        
        assert "Order not found" in str(exc_info.value)
    
    def test_mark_delivered_tenant_isolation(self, order_service, db_session, customer, recipe):
        """Test that orders from other tenants cannot be marked as delivered."""
        # Create two tenants
        tenant1 = TestTenant(chat_id="tenant1_chat")
        tenant2 = TestTenant(chat_id="tenant2_chat")
        db_session.add_all([tenant1, tenant2])
        db_session.commit()
        
        # Create customer for tenant1
        customer1 = TestCustomer(
            tenant_id=tenant1.tenant_id,
            name="Tenant1 Customer",
            phone="1111111111"
        )
        db_session.add(customer1)
        db_session.commit()
        
        # Create recipe for tenant1
        recipe1 = TestRecipe(
            tenant_id=tenant1.tenant_id,
            name="Tenant1 Recipe",
            yield_per_batch=5
        )
        db_session.add(recipe1)
        db_session.commit()
        
        # Create order for tenant1
        tomorrow = date.today() + timedelta(days=1)
        order_data = OrderCreate(
            customer_identifier=customer1.phone,
            delivery_date=tomorrow,
            items=[
                OrderItemCreate(
                    recipe_name=recipe1.name,
                    quantity=1,
                    selling_price=Decimal("15.00")
                )
            ]
        )
        order = order_service.create_order(tenant1.tenant_id, order_data)
        
        # Try to mark order as delivered using tenant2's ID
        with pytest.raises(ValueError) as exc_info:
            order_service.mark_delivered(tenant2.tenant_id, order.order_id)
        
        assert "Order not found" in str(exc_info.value)
    
    def test_mark_delivered_updates_timestamp(self, order_service, tenant, customer, recipe, db_session):
        """Test that marking as delivered updates the updated_at timestamp."""
        # Create an order
        tomorrow = date.today() + timedelta(days=1)
        order_data = OrderCreate(
            customer_identifier=customer.phone,
            delivery_date=tomorrow,
            items=[
                OrderItemCreate(
                    recipe_name=recipe.name,
                    quantity=1,
                    selling_price=Decimal("15.00")
                )
            ]
        )
        order = order_service.create_order(tenant.tenant_id, order_data)
        original_updated_at = order.updated_at
        
        # Wait a moment to ensure timestamp difference
        import time
        time.sleep(0.1)
        
        # Mark as delivered
        updated_order = order_service.mark_delivered(tenant.tenant_id, order.order_id)
        
        # Verify timestamp was updated
        assert updated_order.updated_at > original_updated_at
