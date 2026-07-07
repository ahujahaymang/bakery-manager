"""
Orders domain router for the app-first REST API (``/api/v1/orders``).

A thin HTTP adapter over the existing, unchanged :class:`OrderService`. It
exposes the made-to-order lifecycle for the Orders_Surface (Req 9):

- ``POST   /api/v1/orders``              create an order (initial status
  ``pending``) — Owner+Staff (Req 9.1, 9.7, 9.8).
- ``GET    /api/v1/orders``              list orders, filterable by delivery
  date and/or status — Owner+Staff (Req 9.2, 9.5).
- ``POST   /api/v1/orders/{id}/deliver`` mark a pending order delivered —
  Owner+Staff (Req 9.3).
- ``POST   /api/v1/orders/{id}/cancel``  cancel a pending order, retaining the
  record — Owner+Staff (Req 9.4).
- ``DELETE /api/v1/orders/{id}``         delete an order created in error —
  **Owner only** (Req 9.6).

The router is the choke point for two invariants the service layer does not
enforce on its own:

1. **Status-transition guard** (Req 9.9, Property 20). Only the legal
   transitions ``pending→delivered`` and ``pending→cancelled`` are applied. An
   illegal transition (e.g. ``cancelled→delivered`` or ``delivered→cancelled``)
   is rejected with ``409 invalid_transition`` and the order's status is left
   unchanged. Because :meth:`OrderService.mark_delivered` would set ``delivered``
   from any state, the guard runs *before* the service is invoked.

2. **Value-range validation** (Req 9.8, Property 19). Line-item quantity must be
   within 1..999,999 and unit price within 0.00..9,999,999.99. The shared
   :class:`OrderCreateRequest` schema enforces the lower bounds/required fields;
   this router adds the upper-bound checks the schema omits and surfaces them as
   ``400 validation_error`` with the offending field.

Tenant scope comes strictly from the authenticated device session via
:func:`get_tenant_db_for_user` (Req 19.2, 19.6) — never from request input.

_Requirements: 9.1, 9.2, 9.3, 9.4, 9.5, 9.6, 9.7, 9.8, 9.9_
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response, status
from sqlalchemy.orm import Session

from app.api import errors
from app.api.deps import AuthedUser, get_current_user, get_tenant_db_for_user, require_owner
from app.api.schemas import OrderCreateRequest, serialize_order, serialize_orders
from app.models import Order
from app.services.audit_service import AuditService
from app.services.order_service import OrderCreate, OrderItemCreate, OrderService

router = APIRouter(prefix="/api/v1/orders", tags=["orders"])


# ── Domain constants ──────────────────────────────────────────────────────────

# The three states an order can hold (Req 9.2). Used to validate the status
# filter and to reason about transitions.
_VALID_STATUSES = frozenset({"pending", "delivered", "cancelled"})

# The only legal status transitions (Req 9.3, 9.4, 9.9 / Property 20). Anything
# not in this set is an illegal transition and is rejected, leaving the current
# status unchanged.
_LEGAL_TRANSITIONS = frozenset(
    {
        ("pending", "delivered"),
        ("pending", "cancelled"),
    }
)

# Line-item value ranges (Req 9.8). Quantity is an integer count; price is a
# monetary value with two decimal places.
_QTY_MIN, _QTY_MAX = 1, 999_999
_PRICE_MIN = Decimal("0.00")
_PRICE_MAX = Decimal("9999999.99")


# ── Helpers ─────────────────────────────────────────────────────────────────

def _validate_item_ranges(body: OrderCreateRequest) -> None:
    """
    Enforce Req 9.8 value ranges on every line item.

    The pydantic schema already rejects missing fields and the lower bounds
    (quantity > 0, price >= 0, at least one item). This adds the upper-bound
    checks the schema does not express, so an out-of-range quantity or price is
    rejected with ``400 validation_error`` naming the field and no order is
    created.
    """
    for item in body.items:
        if item.quantity < _QTY_MIN or item.quantity > _QTY_MAX:
            raise errors.ValidationError(
                detail=(
                    f"Quantity must be between {_QTY_MIN} and {_QTY_MAX}. "
                    f"You provided: {item.quantity}"
                ),
                field="quantity",
            )
        if item.selling_price < _PRICE_MIN or item.selling_price > _PRICE_MAX:
            raise errors.ValidationError(
                detail=(
                    f"Unit price must be between {_PRICE_MIN} and {_PRICE_MAX}. "
                    f"You provided: {item.selling_price}"
                ),
                field="selling_price",
            )
        if item.customization_charge < _PRICE_MIN or item.customization_charge > _PRICE_MAX:
            raise errors.ValidationError(
                detail=(
                    f"Customization charge must be between {_PRICE_MIN} and "
                    f"{_PRICE_MAX}. You provided: {item.customization_charge}"
                ),
                field="customization_charge",
            )


def _assert_transition(current: str, target: str) -> None:
    """Reject an illegal status transition (Req 9.9), leaving status unchanged."""
    if (current, target) not in _LEGAL_TRANSITIONS:
        raise errors.InvalidTransitionError()


def _get_order_or_404(db: Session, user: AuthedUser, order_id: UUID) -> Order:
    """Fetch a tenant-scoped order or raise ``404 not_found``."""
    order = (
        db.query(Order)
        .filter(Order.order_id == order_id, Order.tenant_id == user.tenant_id)
        .first()
    )
    if order is None:
        raise errors.NotFoundError()
    return order


# ── Routes ────────────────────────────────────────────────────────────────────

@router.post("", status_code=status.HTTP_201_CREATED)
@router.post("/", status_code=status.HTTP_201_CREATED, include_in_schema=False)
def create_order(
    body: OrderCreateRequest,
    user: AuthedUser = Depends(get_current_user),
    db: Session = Depends(get_tenant_db_for_user),
):
    """
    Create a new order with an initial status of ``pending`` (Req 9.1).

    Required fields (customer, at least one line item, delivery date) are
    enforced by the schema and the service; line-item value ranges are enforced
    here (Req 9.7, 9.8). On any validation failure no order record is created.
    """
    _validate_item_ranges(body)

    domain_order = OrderCreate(
        customer_identifier=body.customer_identifier,
        delivery_date=body.delivery_date,
        items=[
            OrderItemCreate(
                recipe_name=item.recipe_name,
                quantity=item.quantity,
                selling_price=item.selling_price,
                customization_charge=item.customization_charge,
                customization_note=item.customization_note,
            )
            for item in body.items
        ],
        delivery_address=body.delivery_address,
    )

    with errors.map_service_errors():
        order = OrderService(db).create_order(user.tenant_id, domain_order)

    return serialize_order(order, user)


@router.get("")
@router.get("/", include_in_schema=False)
def list_orders(
    user: AuthedUser = Depends(get_current_user),
    db: Session = Depends(get_tenant_db_for_user),
    status_filter: Optional[str] = Query(default=None, alias="status"),
    delivery_date: Optional[date] = Query(default=None),
    date_from: Optional[date] = Query(default=None),
    date_to: Optional[date] = Query(default=None),
):
    """
    List the tenant's orders, optionally filtered by status and/or delivery date
    (Req 9.2, 9.5 / Property 21).

    Filters compose as a conjunction: ``status`` (exact), ``delivery_date``
    (exact), and an inclusive ``date_from``/``date_to`` range. The result is
    exactly the orders satisfying every supplied predicate, ordered by delivery
    date ascending.
    """
    if status_filter is not None:
        normalized = status_filter.strip().lower()
        if normalized not in _VALID_STATUSES:
            raise errors.ValidationError(
                detail=(
                    "Status must be one of: "
                    f"{', '.join(sorted(_VALID_STATUSES))}."
                ),
                field="status",
            )
        status_filter = normalized

    query = db.query(Order).filter(Order.tenant_id == user.tenant_id)
    if status_filter is not None:
        query = query.filter(Order.status == status_filter)
    if delivery_date is not None:
        query = query.filter(Order.delivery_date == delivery_date)
    if date_from is not None:
        query = query.filter(Order.delivery_date >= date_from)
    if date_to is not None:
        query = query.filter(Order.delivery_date <= date_to)

    orders = query.order_by(Order.delivery_date.asc()).all()
    return serialize_orders(orders, user)


@router.post("/{order_id}/deliver")
def deliver_order(
    order_id: UUID,
    user: AuthedUser = Depends(get_current_user),
    db: Session = Depends(get_tenant_db_for_user),
):
    """
    Mark a pending order as delivered (Req 9.3).

    The transition guard rejects any non-``pending`` order (e.g. a cancelled
    one) with ``409 invalid_transition`` before the service runs, so its status
    is left unchanged (Req 9.9).
    """
    order = _get_order_or_404(db, user, order_id)
    _assert_transition(order.status, "delivered")

    with errors.map_service_errors():
        order = OrderService(db).mark_delivered(user.tenant_id, order_id)

    return serialize_order(order, user)


@router.post("/{order_id}/cancel")
def cancel_order(
    order_id: UUID,
    user: AuthedUser = Depends(get_current_user),
    db: Session = Depends(get_tenant_db_for_user),
):
    """
    Cancel a pending order and retain the record (Req 9.4).

    The transition guard rejects cancelling a delivered order with
    ``409 invalid_transition`` (Req 9.9). A successful cancel flips the status to
    ``cancelled`` and logs the change; the order row itself is retained.
    """
    order = _get_order_or_404(db, user, order_id)
    _assert_transition(order.status, "cancelled")

    old_status = order.status
    order.status = "cancelled"
    order.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(order)

    AuditService(db).log_change(
        tenant_id=user.tenant_id,
        table_name="orders",
        record_id=order.order_id,
        operation_type="UPDATE",
        old_values={"status": old_status},
        new_values={"status": "cancelled"},
    )

    return serialize_order(order, user)


@router.delete("/{order_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_order(
    order_id: UUID,
    user: AuthedUser = Depends(require_owner),
    db: Session = Depends(get_tenant_db_for_user),
):
    """
    Delete an order created in error — Owner only (Req 9.6).

    Gated by :func:`require_owner`, so a Staff request is rejected with 403
    before any lookup. Deleting the order cascades to its line items and
    payments; the deletion is recorded in the audit log.
    """
    order = _get_order_or_404(db, user, order_id)

    old_status = order.status
    db.delete(order)
    db.commit()

    AuditService(db).log_change(
        tenant_id=user.tenant_id,
        table_name="orders",
        record_id=order_id,
        operation_type="DELETE",
        old_values={"status": old_status},
        new_values=None,
    )

    return Response(status_code=status.HTTP_204_NO_CONTENT)
