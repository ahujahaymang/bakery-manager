"""
Database models for the Bakery Operations Telegram Bot.

This module defines all SQLAlchemy ORM models for the application,
including tenants, customers, inventory, recipes, orders, payments, and audit logs.
All models include tenant_id for data isolation.
"""

from sqlalchemy import Column, String, Integer, Date, DateTime, ForeignKey, UniqueConstraint, Numeric, Text
from sqlalchemy.types import TypeDecorator
from sqlalchemy.orm import relationship
import uuid
import json
from datetime import datetime

from app.database import Base
from app.config import settings


# ── Portable UUID type ────────────────────────────────────────────────────────
# PostgreSQL: native UUID column
# SQLite: stored as VARCHAR(36) string

class PortableUUID(TypeDecorator):
    """UUID stored natively on Postgres, as VARCHAR(36) on SQLite."""
    impl = String(36)
    cache_ok = True

    def load_dialect_impl(self, dialect):
        if dialect.name == 'postgresql':
            from sqlalchemy.dialects.postgresql import UUID as PG_UUID
            return dialect.type_descriptor(PG_UUID(as_uuid=True))
        return dialect.type_descriptor(String(36))

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        if dialect.name == 'postgresql':
            return value  # psycopg2 handles UUID objects natively
        return str(value)  # SQLite: store as string

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        if isinstance(value, uuid.UUID):
            return value
        return uuid.UUID(str(value))


# ── Portable JSON type ────────────────────────────────────────────────────────
# PostgreSQL: native JSONB
# SQLite: stored as TEXT

class PortableJSON(TypeDecorator):
    """JSONB on Postgres, TEXT on SQLite."""
    impl = Text
    cache_ok = True

    def load_dialect_impl(self, dialect):
        if dialect.name == 'postgresql':
            from sqlalchemy.dialects.postgresql import JSONB
            return dialect.type_descriptor(JSONB())
        return dialect.type_descriptor(Text())

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        if dialect.name == 'postgresql':
            return value
        return json.dumps(value)

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        if dialect.name == 'postgresql' and not isinstance(value, str):
            return value  # already parsed by psycopg2
        return json.loads(value)


class Tenant(Base):
    """
    Tenant model representing a business organization.
    Each tenant is isolated and identified by a unique Telegram chat_id.
    """
    __tablename__ = "tenants"

    tenant_id = Column(PortableUUID(), primary_key=True, default=uuid.uuid4)
    chat_id = Column(String, unique=True, nullable=False, index=True)
    business_name = Column(String, nullable=True)
    country = Column(String, nullable=True)

    # Subscription
    # status: "pending" | "trial" | "active" | "expired"
    # pending  = new user, onboarding not complete
    # trial    = 7-day free trial running
    # active   = paid subscription
    # expired  = trial or subscription ended, access blocked
    subscription_status = Column(String, nullable=False, default="pending")
    trial_started_at = Column(DateTime, nullable=True)
    subscription_expires_at = Column(DateTime, nullable=True)

    # Instagram integration (optional)
    instagram_account_id = Column(String, nullable=True)    # Instagram user/page ID
    instagram_access_token = Column(String, nullable=True)  # long-lived access token

    # Order template — owner's custom order form structure (plain text)
    # Used by the agent to parse pasted order forms without asking for each field
    order_template = Column(Text, nullable=True)

    # Razorpay integration — per-tenant API keys (owner's own Razorpay account)
    razorpay_key_id     = Column(String, nullable=True)   # public key
    razorpay_key_secret = Column(String, nullable=True)   # secret key (encrypt at rest in Phase 2)

    # Primary messaging platform: "telegram" | "whatsapp"
    # Instagram order notifications are sent via this platform
    messaging_platform = Column(String, nullable=False, default="telegram")

    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)
    
    # Relationships (used when operating on a tenant's own business DB)
    customers = relationship("Customer", back_populates="tenant")
    inventory_items = relationship("InventoryItem", back_populates="tenant")
    recipes = relationship("Recipe", back_populates="tenant")
    orders = relationship("Order", back_populates="tenant")
    payments = relationship("Payment", back_populates="tenant")
    audit_logs = relationship("AuditLog", back_populates="tenant")
    products = relationship("Product", back_populates="tenant")
    purchase_expenses = relationship("PurchaseExpense", back_populates="tenant")
    conversation_messages = relationship("ConversationMessage", back_populates="tenant")
    booth_sessions = relationship("BoothSession", back_populates="tenant")


class Customer(Base):
    """
    Customer model representing bakery customers.
    Phone number is unique per tenant.
    """
    __tablename__ = "customers"
    
    customer_id = Column(PortableUUID(), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(PortableUUID(), ForeignKey("tenants.tenant_id"), nullable=False, index=True)
    name = Column(String, nullable=False)
    phone = Column(String, nullable=False)
    address = Column(String, nullable=True)  # optional default delivery address
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
    
    item_id = Column(PortableUUID(), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(PortableUUID(), ForeignKey("tenants.tenant_id"), nullable=False, index=True)
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
    
    recipe_id = Column(PortableUUID(), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(PortableUUID(), ForeignKey("tenants.tenant_id"), nullable=False, index=True)
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
    product = relationship("Product", back_populates="recipe", uselist=False)  # one product per recipe


class RecipeComponent(Base):
    """
    Recipe component model linking recipes to inventory items.
    Defines the quantity and type (ingredient or packaging) of each component.
    """
    __tablename__ = "recipe_components"
    
    component_id = Column(PortableUUID(), primary_key=True, default=uuid.uuid4)
    recipe_id = Column(PortableUUID(), ForeignKey("recipes.recipe_id"), nullable=False, index=True)
    item_id = Column(PortableUUID(), ForeignKey("inventory_items.item_id"), nullable=False, index=True)
    quantity = Column(Numeric(10, 2), nullable=False)
    type = Column(String, nullable=False)  # "ingredient" or "packaging"
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    
    # Relationships
    recipe = relationship("Recipe", back_populates="recipe_components")
    inventory_item = relationship("InventoryItem", back_populates="recipe_components")


class Order(Base):
    """
    Order model representing customer orders.
    Tracks delivery date, delivery address, and status.
    """
    __tablename__ = "orders"
    
    order_id = Column(PortableUUID(), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(PortableUUID(), ForeignKey("tenants.tenant_id"), nullable=False, index=True)
    customer_id = Column(PortableUUID(), ForeignKey("customers.customer_id"), nullable=False, index=True)
    delivery_date = Column(Date, nullable=False)
    delivery_address = Column(String, nullable=True)  # specific address for this order
    status = Column(String, nullable=False, default="pending")  # "pending", "delivered", "cancelled"
    # NULL for regular chat orders; set for booth orders to link to the event session
    booth_session_id = Column(PortableUUID(), ForeignKey("booth_sessions.session_id"), nullable=True, index=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)
    
    # Relationships
    tenant = relationship("Tenant", back_populates="orders")
    customer = relationship("Customer", back_populates="orders")
    order_items = relationship("OrderItem", back_populates="order", cascade="all, delete-orphan")
    payments = relationship("Payment", back_populates="order", cascade="all, delete-orphan")
    booth_session = relationship("BoothSession", back_populates="orders")


class OrderItem(Base):
    """
    Order item model representing individual items in an order.
    Links orders to recipes with quantity and selling price.
    Stores recipe_name for display even when recipe_id is NULL.

    customization_charge: extra charge for this item (e.g. fondant decoration).
    The invoice shows (selling_price + customization_charge) as the unit price —
    customization is not shown separately.
    """
    __tablename__ = "order_items"
    
    order_item_id = Column(PortableUUID(), primary_key=True, default=uuid.uuid4)
    order_id = Column(PortableUUID(), ForeignKey("orders.order_id"), nullable=False, index=True)
    recipe_id = Column(PortableUUID(), ForeignKey("recipes.recipe_id"), nullable=True, index=True)
    recipe_name = Column(String(255), nullable=False)  # Store name for display
    quantity = Column(Integer, nullable=False)
    selling_price = Column(Numeric(10, 2), nullable=False)
    customization_charge = Column(Numeric(10, 2), nullable=False, default=0)  # extra per-item charge
    customization_note = Column(String, nullable=True)   # e.g. "fondant decoration"
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
    
    payment_id = Column(PortableUUID(), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(PortableUUID(), ForeignKey("tenants.tenant_id"), nullable=False, index=True)
    order_id = Column(PortableUUID(), ForeignKey("orders.order_id"), nullable=False, index=True)
    amount = Column(Numeric(10, 2), nullable=False)
    method = Column(String, nullable=False)  # "Cash", "UPI", "Razorpay", "Paytm", "Bank Transfer"
    # Razorpay payment ID for reconciliation — set only for Razorpay payments
    razorpay_payment_id = Column(String, nullable=True, index=True)
    # Payment status: "completed" for cash/UPI, "pending"→"completed" for Razorpay
    status = Column(String, nullable=False, default="completed")
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
    
    log_id = Column(PortableUUID(), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(PortableUUID(), ForeignKey("tenants.tenant_id"), nullable=False, index=True)
    table_name = Column(String, nullable=False)
    record_id = Column(PortableUUID(), nullable=False)
    operation_type = Column(String, nullable=False)  # "UPDATE" or "DELETE"
    old_values = Column(PortableJSON())
    new_values = Column(PortableJSON())
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    
    # Relationships
    tenant = relationship("Tenant", back_populates="audit_logs")


class ConversationMessage(Base):
    """
    Persisted conversation history for a tenant's chat.

    Replaces the in-memory _history dict in RequestHandler so that
    context survives server restarts and is queryable for debugging.

    chat_id is stored alongside tenant_id because the admin chat may
    operate multiple tenants — each (tenant_id, chat_id) pair has its
    own independent history.
    """
    __tablename__ = "conversation_messages"

    message_id = Column(PortableUUID(), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(PortableUUID(), ForeignKey("tenants.tenant_id"), nullable=False, index=True)
    chat_id = Column(String, nullable=False, index=True)
    role = Column(String, nullable=False)       # "user" | "assistant" | "tool"
    content = Column(Text, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    # Relationships
    tenant = relationship("Tenant", back_populates="conversation_messages")


class BoothSession(Base):
    """
    A Register Mode session — groups all point-of-sale transactions.

    mode = "regular" : daily register (no fixed end date)
    mode = "event"   : multi-day event (e.g. Delhi Food Fest, 3 days)
                       ends_at is set; auto-ended when that time passes.

    One active session per tenant at a time (enforced in BoothService).
    ended_at=NULL means the session is still active.
    """
    __tablename__ = "booth_sessions"

    session_id    = Column(PortableUUID(), primary_key=True, default=uuid.uuid4)
    tenant_id     = Column(PortableUUID(), ForeignKey("tenants.tenant_id"), nullable=False, index=True)
    name          = Column(String, nullable=False)
    mode          = Column(String, nullable=False, default="regular")  # "regular" | "event"
    duration_days = Column(Integer, nullable=True)   # only for event mode
    started_at    = Column(DateTime, nullable=False, default=datetime.utcnow)
    ends_at       = Column(DateTime, nullable=True)  # scheduled end (event mode)
    ended_at      = Column(DateTime, nullable=True)  # actual end (NULL = still active)
    created_at    = Column(DateTime, nullable=False, default=datetime.utcnow)

    # Relationships
    tenant = relationship("Tenant", back_populates="booth_sessions")
    items  = relationship("BoothSessionItem", back_populates="session",
                          cascade="all, delete-orphan")
    orders = relationship("Order", back_populates="booth_session")


class BoothSessionItem(Base):
    """
    A product variant available for sale in a booth session.

    booth_price may differ from the catalog price (event pricing).
    stock_qty=NULL means unlimited stock.
    sold_qty is incremented atomically on each checkout.
    """
    __tablename__ = "booth_session_items"

    item_id     = Column(PortableUUID(), primary_key=True, default=uuid.uuid4)
    session_id  = Column(PortableUUID(), ForeignKey("booth_sessions.session_id"),
                         nullable=False, index=True)
    variant_id  = Column(PortableUUID(), ForeignKey("product_variants.variant_id"),
                         nullable=False)
    booth_price = Column(Numeric(10, 2), nullable=False)
    stock_qty   = Column(Integer, nullable=True)           # NULL = unlimited
    sold_qty    = Column(Integer, nullable=False, default=0)
    created_at  = Column(DateTime, nullable=False, default=datetime.utcnow)

    # Relationships
    session = relationship("BoothSession", back_populates="items")
    variant = relationship("ProductVariant")


class Product(Base):
    """
    Product catalog item — a finished product the owner sells.

    Distinct from Recipe (which tracks ingredients and cost).
    Prices live in ProductVariant — one product can have multiple size/weight
    variants (e.g. "250g = ₹400, 500g = ₹800").

    Optionally linked to a Recipe for cost/margin calculation.
    """
    __tablename__ = "products"

    product_id = Column(PortableUUID(), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(PortableUUID(), ForeignKey("tenants.tenant_id"), nullable=False, index=True)
    name = Column(String, nullable=False)
    description = Column(Text, nullable=True)
    category = Column(String, nullable=True)     # e.g. "Gourmet Cookies", "Desserts"
    image_url = Column(String, nullable=True)    # Telegram file_id or S3 URL

    # Optional link to a Recipe — NULL means no recipe associated
    recipe_id = Column(PortableUUID(), ForeignKey("recipes.recipe_id"), nullable=True, index=True)

    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    __table_args__ = (
        UniqueConstraint('tenant_id', 'name', name='uq_product_tenant_name'),
    )

    # Relationships
    tenant = relationship("Tenant", back_populates="products")
    recipe = relationship("Recipe", back_populates="product")
    variants = relationship("ProductVariant", back_populates="product",
                            cascade="all, delete-orphan", order_by="ProductVariant.price")


class ProductVariant(Base):
    """
    A size/weight/pack variant of a product with its own price.

    Examples:
      Oatmeal Raisin Cookies — 250 gms → ₹400
      Oatmeal Raisin Cookies — 500 gms → ₹800
      Plain Chocolate Brownie — ½ kg   → ₹600
      Plain Chocolate Brownie — 1 kg   → ₹1200
      Vanilla Muffin          — per piece → ₹50

    Single-price products have exactly one variant.
    """
    __tablename__ = "product_variants"

    variant_id = Column(PortableUUID(), primary_key=True, default=uuid.uuid4)
    product_id = Column(PortableUUID(), ForeignKey("products.product_id"), nullable=False, index=True)
    size_label = Column(String, nullable=False)      # e.g. "250 gms", "½ kg", "per piece", "Pack of 6"
    price = Column(Numeric(10, 2), nullable=False)

    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    __table_args__ = (
        UniqueConstraint('product_id', 'size_label', name='uq_variant_product_size'),
    )

    # Relationships
    product = relationship("Product", back_populates="variants")


class PurchaseExpense(Base):
    """
    Records any money the owner spends for the business.

    Categories:
      ingredients  — flour, butter, chocolate, etc.
      packaging    — boxes, ribbons, bags
      equipment    — one-time capital items (oven, mixer, mould)
      utilities    — electricity, gas, water
      rent         — kitchen/storage rent
      marketing    — ads, flyers, photoshoots
      other        — anything that doesn't fit above

    is_capital = True for durable assets (equipment) the owner wants to
    track separately from running costs.

    Created automatically from receipt images, or manually via the agent.
    """
    __tablename__ = "purchase_expenses"

    # Valid categories — enforced at the tool level, stored as plain string
    CATEGORIES = [
        "ingredients", "packaging", "equipment",
        "utilities", "rent", "marketing", "other",
    ]

    expense_id = Column(PortableUUID(), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(PortableUUID(), ForeignKey("tenants.tenant_id"), nullable=False, index=True)
    amount = Column(Numeric(10, 2), nullable=False)
    vendor_name = Column(String, nullable=True)       # shop/vendor name from receipt
    expense_date = Column(Date, nullable=False)        # date from receipt (or today)
    category = Column(String, nullable=False, default="other")  # see CATEGORIES above
    is_capital = Column(String, nullable=False, default="false")  # "true"/"false" (SQLite safe)
    description = Column(String, nullable=True)       # human-readable: "OTG oven 45L", "flour 10kg"
    notes = Column(Text, nullable=True)               # raw items / extra detail
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    # Relationships
    tenant = relationship("Tenant", back_populates="purchase_expenses")
