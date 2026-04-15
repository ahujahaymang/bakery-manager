"""
Database models for the Bakery Operations Telegram Bot.

This module defines all SQLAlchemy ORM models for the application,
including tenants, customers, inventory, recipes, orders, payments, and audit logs.
All models include tenant_id for data isolation.
"""

from sqlalchemy import Column, String, Integer, Date, DateTime, ForeignKey, UniqueConstraint, Numeric
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import relationship
import uuid
from datetime import datetime

from app.database import Base


class Tenant(Base):
    """
    Tenant model representing a bakery organization.
    Each tenant is isolated and identified by a unique Telegram chat_id.
    """
    __tablename__ = "tenants"
    
    tenant_id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    chat_id = Column(String, unique=True, nullable=False, index=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)
    
    # Relationships
    customers = relationship("Customer", back_populates="tenant", cascade="all, delete-orphan")
    inventory_items = relationship("InventoryItem", back_populates="tenant", cascade="all, delete-orphan")
    recipes = relationship("Recipe", back_populates="tenant", cascade="all, delete-orphan")
    orders = relationship("Order", back_populates="tenant", cascade="all, delete-orphan")
    payments = relationship("Payment", back_populates="tenant", cascade="all, delete-orphan")
    audit_logs = relationship("AuditLog", back_populates="tenant", cascade="all, delete-orphan")


class Customer(Base):
    """
    Customer model representing bakery customers.
    Phone number is unique per tenant.
    """
    __tablename__ = "customers"
    
    customer_id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(UUID(as_uuid=True), ForeignKey("tenants.tenant_id"), nullable=False, index=True)
    name = Column(String, nullable=False)
    phone = Column(String, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)
    
    __table_args__ = (
        UniqueConstraint('tenant_id', 'phone', name='uq_customer_tenant_phone'),
    )
    
    # Relationships
    tenant = relationship("Tenant", back_populates="customers")
    orders = relationship("Order", back_populates="customer", cascade="all, delete-orphan")


class InventoryItem(Base):
    """
    Inventory item model for ingredients and packaging materials.
    Item name is unique per tenant.
    """
    __tablename__ = "inventory_items"
    
    item_id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(UUID(as_uuid=True), ForeignKey("tenants.tenant_id"), nullable=False, index=True)
    name = Column(String, nullable=False)
    category = Column(String, nullable=False)  # "ingredient" or "packaging"
    quantity = Column(Numeric(10, 2), nullable=False)
    unit = Column(String, nullable=False)  # "kg", "g", "litre", "ml", "pcs"
    cost_per_unit = Column(Numeric(10, 2), nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)
    
    __table_args__ = (
        UniqueConstraint('tenant_id', 'name', name='uq_inventory_tenant_name'),
    )
    
    # Relationships
    tenant = relationship("Tenant", back_populates="inventory_items")
    recipe_components = relationship("RecipeComponent", back_populates="inventory_item", cascade="all, delete-orphan")


class Recipe(Base):
    """
    Recipe model defining bakery products.
    Recipe name is unique per tenant.
    """
    __tablename__ = "recipes"
    
    recipe_id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(UUID(as_uuid=True), ForeignKey("tenants.tenant_id"), nullable=False, index=True)
    name = Column(String, nullable=False)
    yield_per_batch = Column(Integer, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)
    
    __table_args__ = (
        UniqueConstraint('tenant_id', 'name', name='uq_recipe_tenant_name'),
    )
    
    # Relationships
    tenant = relationship("Tenant", back_populates="recipes")
    recipe_components = relationship("RecipeComponent", back_populates="recipe", cascade="all, delete-orphan")
    order_items = relationship("OrderItem", back_populates="recipe")


class RecipeComponent(Base):
    """
    Recipe component model linking recipes to inventory items.
    Defines the quantity and type (ingredient or packaging) of each component.
    """
    __tablename__ = "recipe_components"
    
    component_id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    recipe_id = Column(UUID(as_uuid=True), ForeignKey("recipes.recipe_id"), nullable=False, index=True)
    item_id = Column(UUID(as_uuid=True), ForeignKey("inventory_items.item_id"), nullable=False, index=True)
    quantity = Column(Numeric(10, 2), nullable=False)
    type = Column(String, nullable=False)  # "ingredient" or "packaging"
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    
    # Relationships
    recipe = relationship("Recipe", back_populates="recipe_components")
    inventory_item = relationship("InventoryItem", back_populates="recipe_components")


class Order(Base):
    """
    Order model representing customer orders.
    Tracks delivery date and status (pending or delivered).
    """
    __tablename__ = "orders"
    
    order_id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(UUID(as_uuid=True), ForeignKey("tenants.tenant_id"), nullable=False, index=True)
    customer_id = Column(UUID(as_uuid=True), ForeignKey("customers.customer_id"), nullable=False, index=True)
    delivery_date = Column(Date, nullable=False)
    status = Column(String, nullable=False, default="pending")  # "pending" or "delivered"
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)
    
    # Relationships
    tenant = relationship("Tenant", back_populates="orders")
    customer = relationship("Customer", back_populates="orders")
    order_items = relationship("OrderItem", back_populates="order", cascade="all, delete-orphan")
    payments = relationship("Payment", back_populates="order", cascade="all, delete-orphan")


class OrderItem(Base):
    """
    Order item model representing individual items in an order.
    Links orders to recipes with quantity and selling price.
    Stores recipe_name for display even when recipe_id is NULL.
    """
    __tablename__ = "order_items"
    
    order_item_id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    order_id = Column(UUID(as_uuid=True), ForeignKey("orders.order_id"), nullable=False, index=True)
    recipe_id = Column(UUID(as_uuid=True), ForeignKey("recipes.recipe_id"), nullable=True, index=True)
    recipe_name = Column(String(255), nullable=False)  # Store name for display
    quantity = Column(Integer, nullable=False)
    selling_price = Column(Numeric(10, 2), nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    
    # Relationships
    order = relationship("Order", back_populates="order_items")
    recipe = relationship("Recipe", back_populates="order_items")


class Payment(Base):
    """
    Payment model for tracking order payments.
    Supports multiple payment methods: Cash, Paytm, Bank Transfer.
    """
    __tablename__ = "payments"
    
    payment_id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(UUID(as_uuid=True), ForeignKey("tenants.tenant_id"), nullable=False, index=True)
    order_id = Column(UUID(as_uuid=True), ForeignKey("orders.order_id"), nullable=False, index=True)
    amount = Column(Numeric(10, 2), nullable=False)
    method = Column(String, nullable=False)  # "Cash", "Paytm", "Bank Transfer"
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)
    
    # Relationships
    tenant = relationship("Tenant", back_populates="payments")
    order = relationship("Order", back_populates="payments")


class AuditLog(Base):
    """
    Audit log model for tracking data modifications.
    Records UPDATE and DELETE operations with old and new values.
    """
    __tablename__ = "audit_logs"
    
    log_id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(UUID(as_uuid=True), ForeignKey("tenants.tenant_id"), nullable=False, index=True)
    table_name = Column(String, nullable=False)
    record_id = Column(UUID(as_uuid=True), nullable=False)
    operation_type = Column(String, nullable=False)  # "UPDATE" or "DELETE"
    old_values = Column(JSONB)
    new_values = Column(JSONB)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    
    # Relationships
    tenant = relationship("Tenant", back_populates="audit_logs")
