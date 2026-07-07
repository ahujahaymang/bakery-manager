"""
Pydantic request/response schemas and role-aware serializers for the app-first
REST API.

This module is the single place that shapes JSON payloads for the versioned REST
surface. It has two responsibilities:

1. **Request models** — validate/parse incoming JSON into typed objects the
   domain routers hand to the existing (unchanged) service layer. Field names
   mirror the service-layer dataclasses and ORM models exactly, so a router can
   forward parsed values without renaming.

2. **Response models + serializer functions** — turn ORM rows / service
   dataclasses into JSON-ready dicts. Every serializer takes the authenticated
   principal (``AuthedUser``) and, **for the Staff role, omits cost /
   cost-per-unit / profit / other financial fields from the serialized payload**
   (Req 5.4, 10.7, 11.7).

Role-aware hiding here is *defense-in-depth*. The primary control is the
route-level role gate (`require_owner` and the domain routers), which rejects a
Staff request for cost/financial reads with 403 **before** the service runs
(design §5, Property 2). Serializer stripping guarantees that even on the
Owner+Staff shared endpoints (e.g. the inventory list), a Staff payload never
carries a cost field anywhere in its structure (Property 3).

`AuthedUser` is defined by ``app/api/deps.py`` (a sibling task). To keep this
module importable regardless of load order — and to avoid a circular import if
``deps`` ever imports ``schemas`` — we depend only on a small structural
:class:`AuthedUserLike` protocol (any object exposing ``role`` / ``tenant_id`` /
``user_id``). The real ``AuthedUser`` dataclass satisfies it, and the serializers
never import it at runtime.

_Requirements: 5.4, 10.7, 11.7_
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Protocol, runtime_checkable
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

if TYPE_CHECKING:  # pragma: no cover - typing aid only
    # Prefer the concrete dataclass for type-checkers once deps.py exists.
    try:
        from app.api.deps import AuthedUser  # noqa: F401
    except Exception:  # deps.py not present yet
        AuthedUser = Any  # type: ignore


# ── Authenticated principal (structural) ──────────────────────────────────────

@runtime_checkable
class AuthedUserLike(Protocol):
    """
    Structural type for the authenticated principal used by serializers.

    ``app/api/deps.py`` resolves the device session token into a concrete
    ``AuthedUser`` carrying these attributes (design §2). Depending only on the
    shape — rather than importing the class — keeps ``schemas`` free of a hard
    dependency on ``deps`` and safe against import ordering.
    """

    user_id: UUID
    tenant_id: UUID
    role: str
    device_id: UUID


# ── Role helpers ──────────────────────────────────────────────────────────────

# The two roles a User can hold (Req 5.1). Matched case-insensitively so the
# helpers are robust to how the token resolver stores the role.
ROLE_OWNER = "owner"
ROLE_STAFF = "staff"


def role_of(user: "AuthedUserLike") -> str:
    """Return the principal's role, normalized to lowercase ('' if absent)."""
    role = getattr(user, "role", None)
    return str(role).strip().lower() if role else ""


def is_staff(user: "AuthedUserLike") -> bool:
    """True when the principal holds the Staff role (cost/financials hidden)."""
    return role_of(user) == ROLE_STAFF


def is_owner(user: "AuthedUserLike") -> bool:
    """True when the principal holds the Owner role (full financial access)."""
    return role_of(user) == ROLE_OWNER


# ── Financial-field stripping (Req 5.4, 10.7, 11.7) ───────────────────────────

# Field names that carry cost / cost-per-unit / profit information. For a Staff
# principal these keys are removed from the serialized payload *anywhere* they
# appear (including nested items/lists), so no cost value can leak through a
# shared endpoint (Property 3).
#
# Deliberately excluded: ``amount``, ``selling_price``, ``price``, invoice
# totals, and payment amounts. Those are *sale* figures Staff legitimately need
# to operate the Sell surface — they are not cost/profit data. The Expenses
# surface (whose ``amount`` *is* a cost) is Owner-only and route-gated, so its
# serializer never runs for Staff (design §5).
STAFF_HIDDEN_FIELDS = frozenset(
    {
        "cost",
        "cost_per_unit",
        "unit_cost",
        "ingredient_cost",
        "packaging_cost",
        "total_cost",
        "profit",
        "profit_margin",
        "margin",
        "markup",
    }
)


def _strip_keys(value: Any, hidden: frozenset) -> Any:
    """Recursively drop ``hidden`` keys from dicts within ``value``."""
    if isinstance(value, dict):
        return {
            key: _strip_keys(val, hidden)
            for key, val in value.items()
            if key not in hidden
        }
    if isinstance(value, (list, tuple)):
        return [_strip_keys(item, hidden) for item in value]
    return value


def apply_role_visibility(data: Any, user: "AuthedUserLike") -> Any:
    """
    Return ``data`` with financial fields removed when ``user`` is Staff.

    Owner payloads pass through unchanged. Staff payloads have every
    :data:`STAFF_HIDDEN_FIELDS` key removed at any depth, so cost / cost-per-unit
    / profit values are absent from the payload entirely rather than nulled
    (Req 10.7, 11.7).
    """
    if not is_staff(user):
        return data
    return _strip_keys(data, STAFF_HIDDEN_FIELDS)


# ── Base models ───────────────────────────────────────────────────────────────

class _ApiModel(BaseModel):
    """Base for all API models: build from ORM/attribute objects, strip whitespace."""

    model_config = ConfigDict(from_attributes=True, str_strip_whitespace=True)


# =============================================================================
# Customers  (Req 12)
# =============================================================================

class CustomerCreateRequest(_ApiModel):
    """Body for ``POST /api/v1/customers`` — mirrors ``CustomerService.create_customer``."""

    name: str = Field(min_length=1, max_length=100)
    phone: str = Field(min_length=8, max_length=20)
    address: Optional[str] = None


class CustomerResponse(_ApiModel):
    """Serialized customer. Carries no financial fields."""

    customer_id: UUID
    tenant_id: UUID
    name: str
    phone: str
    address: Optional[str] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


def serialize_customer(customer: Any, user: "AuthedUserLike") -> Dict[str, Any]:
    """Serialize a Customer ORM row for ``user`` (no financial fields to hide)."""
    data = CustomerResponse.model_validate(customer).model_dump()
    return apply_role_visibility(data, user)


def serialize_customers(customers: Any, user: "AuthedUserLike") -> List[Dict[str, Any]]:
    """Serialize a list of Customer rows for ``user``."""
    return [serialize_customer(c, user) for c in customers]


# =============================================================================
# Inventory  (Req 10) — cost_per_unit hidden for Staff (Req 10.7)
# =============================================================================

class InventoryItemCreateRequest(_ApiModel):
    """Body for ``POST /api/v1/inventory`` — mirrors ``InventoryService.create_item``."""

    name: str = Field(min_length=1, max_length=100)
    category: str = Field(min_length=1, max_length=50)
    quantity: Decimal = Field(ge=0, le=Decimal("999999.99"))
    unit: str = Field(min_length=1, max_length=20)
    cost_per_unit: Decimal = Field(ge=0, le=Decimal("999999.99"))


class InventoryItemUpdateRequest(_ApiModel):
    """Body for ``PATCH /api/v1/inventory/{id}`` — quantity and/or cost update."""

    quantity: Optional[Decimal] = Field(default=None, ge=0, le=Decimal("999999.99"))
    cost_per_unit: Optional[Decimal] = Field(default=None, ge=0, le=Decimal("999999.99"))


class InventoryItemResponse(_ApiModel):
    """
    Serialized inventory item. ``cost_per_unit`` is a cost field and is omitted
    from the payload for Staff by :func:`serialize_inventory_item` (Req 10.7).
    """

    item_id: UUID
    tenant_id: UUID
    name: str
    category: str
    quantity: Decimal
    unit: str
    cost_per_unit: Decimal
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


def serialize_inventory_item(item: Any, user: "AuthedUserLike") -> Dict[str, Any]:
    """Serialize an InventoryItem for ``user``; hides ``cost_per_unit`` for Staff (Req 10.7)."""
    data = InventoryItemResponse.model_validate(item).model_dump()
    return apply_role_visibility(data, user)


def serialize_inventory_items(items: Any, user: "AuthedUserLike") -> List[Dict[str, Any]]:
    """Serialize a list of InventoryItem rows for ``user`` (cost hidden for Staff)."""
    return [serialize_inventory_item(i, user) for i in items]


# =============================================================================
# Recipes  (Req 11) — cost-per-unit hidden for Staff (Req 11.7)
# =============================================================================

class RecipeCreateRequest(_ApiModel):
    """Body for ``POST /api/v1/recipes`` — mirrors ``RecipeService.create_recipe``."""

    name: str = Field(min_length=1, max_length=100)
    yield_per_batch: int = Field(gt=0, le=999999)


class RecipeComponentCreateRequest(_ApiModel):
    """
    Body for ``POST /api/v1/recipes/{id}/components``.

    ``component_type`` selects whether the inventory item is an ``ingredient``
    or ``packaging`` component; the service records it on the ``RecipeComponent``
    and uses it to split ingredient vs packaging cost (Req 11.2, 11.3).
    """

    item_name: str = Field(min_length=1, max_length=100)
    quantity: Decimal = Field(gt=0, le=Decimal("999999"))
    component_type: str = Field(pattern="^(ingredient|packaging)$")


class RecipeResponse(_ApiModel):
    """Serialized recipe metadata (no cost fields; cost lives in the cost endpoint)."""

    recipe_id: UUID
    tenant_id: UUID
    name: str
    yield_per_batch: int
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class RecipeCostResponse(_ApiModel):
    """
    Serialized recipe cost breakdown (mirrors ``recipe_service.RecipeCost``).

    Every numeric field here is cost/cost-per-unit data. The recipe-cost read
    endpoint is Owner-only and rejected for Staff before the service runs
    (Req 11.7); this serializer additionally strips those fields as
    defense-in-depth should it ever be reached for a Staff principal.
    """

    recipe_name: str
    yield_per_batch: int
    ingredient_cost: Decimal
    packaging_cost: Decimal
    unit_cost: Decimal


def serialize_recipe(recipe: Any, user: "AuthedUserLike") -> Dict[str, Any]:
    """Serialize a Recipe ORM row for ``user``."""
    data = RecipeResponse.model_validate(recipe).model_dump()
    return apply_role_visibility(data, user)


def serialize_recipes(recipes: Any, user: "AuthedUserLike") -> List[Dict[str, Any]]:
    """Serialize a list of Recipe rows for ``user``."""
    return [serialize_recipe(r, user) for r in recipes]


def serialize_recipe_cost(cost: Any, user: "AuthedUserLike") -> Dict[str, Any]:
    """
    Serialize a recipe cost breakdown for ``user``.

    For Staff, all cost-per-unit fields are stripped (Req 11.7). In normal flow
    this endpoint is 403 for Staff at the router; stripping is defense-in-depth.
    """
    data = RecipeCostResponse.model_validate(cost).model_dump()
    return apply_role_visibility(data, user)


# =============================================================================
# Orders  (Req 9) — selling_price is a sale figure, retained for Staff
# =============================================================================

class OrderItemCreateRequest(_ApiModel):
    """One line item in ``POST /api/v1/orders`` — mirrors ``OrderItemCreate``."""

    recipe_name: str = Field(min_length=1, max_length=255)
    quantity: int = Field(gt=0)
    selling_price: Decimal = Field(ge=0)
    customization_charge: Decimal = Field(default=Decimal("0"), ge=0)
    customization_note: Optional[str] = None


class OrderCreateRequest(_ApiModel):
    """Body for ``POST /api/v1/orders`` — mirrors ``OrderCreate``."""

    customer_identifier: str = Field(min_length=1)
    delivery_date: date
    items: List[OrderItemCreateRequest] = Field(min_length=1)
    delivery_address: Optional[str] = None


class OrderItemResponse(_ApiModel):
    """Serialized order line item. ``selling_price`` is a sale price, not cost."""

    order_item_id: UUID
    recipe_id: Optional[UUID] = None
    recipe_name: str
    quantity: int
    selling_price: Decimal
    customization_charge: Decimal
    customization_note: Optional[str] = None


class OrderResponse(_ApiModel):
    """Serialized order with its line items. Carries no cost/profit fields."""

    order_id: UUID
    tenant_id: UUID
    customer_id: UUID
    customer_name: Optional[str] = None
    delivery_date: date
    delivery_address: Optional[str] = None
    status: str
    booth_session_id: Optional[UUID] = None
    created_by_user_id: Optional[UUID] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    items: List[OrderItemResponse] = Field(default_factory=list)


def serialize_order(order: Any, user: "AuthedUserLike") -> Dict[str, Any]:
    """
    Serialize an Order (with ``order_items`` → ``items``) for ``user``.

    Financial-field stripping still applies (defense-in-depth); no cost/profit
    field exists on the order shape, and sale prices are retained for Staff.
    """
    data = OrderResponse.model_validate(
        {
            "order_id": order.order_id,
            "tenant_id": order.tenant_id,
            "customer_id": order.customer_id,
            "customer_name": getattr(getattr(order, "customer", None), "name", None),
            "delivery_date": order.delivery_date,
            "delivery_address": order.delivery_address,
            "status": order.status,
            "booth_session_id": getattr(order, "booth_session_id", None),
            "created_by_user_id": getattr(order, "created_by_user_id", None),
            "created_at": order.created_at,
            "updated_at": getattr(order, "updated_at", None),
            "items": list(getattr(order, "order_items", []) or []),
        }
    ).model_dump()
    return apply_role_visibility(data, user)


def serialize_orders(orders: Any, user: "AuthedUserLike") -> List[Dict[str, Any]]:
    """Serialize a list of Order rows for ``user``."""
    return [serialize_order(o, user) for o in orders]


# =============================================================================
# Customers' payments & invoices  (Req 8, 13) — sale figures, shown to Staff
# =============================================================================

class PaymentCreateRequest(_ApiModel):
    """Body for recording a payment — mirrors ``PaymentCreate``."""

    order_identifier: str = Field(min_length=1)
    amount: Decimal = Field(gt=0)
    method: str = Field(min_length=1)


class PaymentResponse(_ApiModel):
    """Serialized payment record. ``amount`` is a sale/received figure, not cost."""

    payment_id: UUID
    order_id: UUID
    amount: Decimal
    method: str
    status: str
    razorpay_payment_id: Optional[str] = None
    created_at: Optional[datetime] = None


def serialize_payment(payment: Any, user: "AuthedUserLike") -> Dict[str, Any]:
    """Serialize a Payment row for ``user``."""
    data = PaymentResponse.model_validate(payment).model_dump()
    return apply_role_visibility(data, user)


class InvoiceItemResponse(_ApiModel):
    """One invoice line — mirrors ``invoice_service.InvoiceItem``."""

    description: str
    quantity: int
    unit_price: Decimal
    total: Decimal


class InvoiceResponse(_ApiModel):
    """
    Serialized invoice — mirrors ``invoice_service.InvoiceData``.

    All amounts here are sale/receivable figures (subtotal, tax, amount due),
    not cost or profit, so they are shown to Staff who operate the Sell surface.
    """

    invoice_number: str
    issue_date: date
    delivery_date: date
    items: List[InvoiceItemResponse] = Field(default_factory=list)
    subtotal: Decimal
    tax_rate: Decimal = Decimal("0")
    tax_label: str = ""
    tax_amount: Decimal = Decimal("0")
    amount_paid: Decimal
    amount_due: Decimal
    currency: Optional[str] = None


def serialize_invoice(invoice: Any, user: "AuthedUserLike") -> Dict[str, Any]:
    """Serialize invoice data for ``user``."""
    data = InvoiceResponse.model_validate(invoice).model_dump()
    return apply_role_visibility(data, user)


# =============================================================================
# Expenses  (Req 14) — Owner-only surface (route-gated); schema for completeness
# =============================================================================

class ExpenseCreateRequest(_ApiModel):
    """Body for ``POST /api/v1/expenses`` — mirrors ``PurchaseExpense`` fields."""

    amount: Decimal = Field(ge=Decimal("0.01"), le=Decimal("999999999.99"))
    expense_date: date
    category: str = Field(min_length=1)
    vendor_name: Optional[str] = None
    is_capital: bool = False
    description: Optional[str] = Field(default=None, max_length=500)
    notes: Optional[str] = None


class ExpenseResponse(_ApiModel):
    """
    Serialized expense. The Expenses surface is Owner-only and rejected for
    Staff at the router (Req 14.6), so this serializer runs for Owners in
    practice; ``amount`` is intentionally *not* in :data:`STAFF_HIDDEN_FIELDS`
    (that set targets cost-of-goods/profit fields on shared endpoints).
    """

    expense_id: UUID
    tenant_id: UUID
    amount: Decimal
    expense_date: date
    category: str
    vendor_name: Optional[str] = None
    is_capital: Optional[str] = None
    description: Optional[str] = None
    notes: Optional[str] = None
    created_at: Optional[datetime] = None


def serialize_expense(expense: Any, user: "AuthedUserLike") -> Dict[str, Any]:
    """Serialize a PurchaseExpense row for ``user`` (Owner-only surface)."""
    data = ExpenseResponse.model_validate(expense).model_dump()
    return apply_role_visibility(data, user)


def serialize_expenses(expenses: Any, user: "AuthedUserLike") -> List[Dict[str, Any]]:
    """Serialize a list of PurchaseExpense rows for ``user``."""
    return [serialize_expense(e, user) for e in expenses]
