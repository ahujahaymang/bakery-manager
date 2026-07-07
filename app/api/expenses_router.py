"""
Expenses domain router — ``/api/v1/expenses/*`` (**Owner-only**).

A thin HTTP adapter over the expense service-layer path (the ``PurchaseExpense``
model, the same rows the LLM ``record_expense`` / ``list_expenses`` tools write
and read). No business logic beyond field validation and the date-range filter
lives here: the router parses/validates the request, opens a tenant-scoped DB
session derived strictly from the authenticated device token, persists/reads
``PurchaseExpense`` rows, and shapes the response with the role-aware
serializers in ``app/api/schemas.py``.

Endpoints (all **Owner-only** — the Expenses surface is hidden for Staff and is
route-gated by :func:`require_owner`, Req 14.6):

- ``POST /api/v1/expenses``  → create an expense (Req 14.1, 14.2, 14.3)
- ``GET  /api/v1/expenses``  → list with category + inclusive date range (Req 14.4, 14.5)

Create validation (Req 14.1, 14.2, 14.3):

- ``amount`` must be between 0.01 and 999,999,999.99 (enforced by
  :class:`ExpenseCreateRequest`).
- ``category`` must be one of the predefined :data:`PurchaseExpense.CATEGORIES`
  (a missing category is rejected by the request model; an unrecognized one is
  rejected here with a 400 ``validation_error`` naming the ``category`` field).
- ``description`` must be at most 500 characters (enforced by the request model).
- An invalid submission creates **no** record and returns a field-identifying
  error (Req 14.2).
- ``is_capital`` is preserved on the stored record (Req 14.3); the model stores
  the flag as the SQLite-safe string ``"true"``/``"false"``.

List filtering (Req 14.4, 14.5):

- An optional ``category`` filter and an optional inclusive ``start_date`` /
  ``end_date`` range narrow the results; the range is inclusive of both
  endpoints.
- If both dates are supplied and ``start_date`` is later than ``end_date``, the
  filter is rejected with a 400 ``validation_error`` naming the ``date`` field
  and no records are returned (Req 14.5).

_Requirements: 14.1, 14.2, 14.3, 14.4, 14.5, 14.6_
"""

from __future__ import annotations

from datetime import date
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from app.api import errors
from app.api.deps import AuthedUser, get_tenant_db_for_user, require_owner
from app.api.schemas import (
    ExpenseCreateRequest,
    serialize_expense,
    serialize_expenses,
)
from app.models import PurchaseExpense

router = APIRouter(prefix="/api/v1/expenses", tags=["expenses"])


@router.post("", status_code=status.HTTP_201_CREATED)
def create_expense(
    body: ExpenseCreateRequest,
    user: AuthedUser = Depends(require_owner),
    db: Session = Depends(get_tenant_db_for_user),
) -> Dict[str, Any]:
    """
    Record a business expense for the acting tenant — **Owner only**
    (Req 14.1, 14.2, 14.3, 14.6).

    :func:`require_owner` rejects Staff with 403 before this runs, so the
    Expenses surface never persists for a Staff principal (Req 14.6). The amount
    range (0.01–999,999,999.99) and the 500-character description limit are
    enforced by :class:`ExpenseCreateRequest`; the category is validated here
    against the predefined :data:`PurchaseExpense.CATEGORIES`, rejecting an
    unrecognized value with a 400 ``validation_error`` naming ``category`` and
    persisting nothing (Req 14.2). On success the persisted row — including its
    assigned ``expense_id`` and its preserved capital-asset flag (Req 14.3) —
    is returned.
    """
    if body.category not in PurchaseExpense.CATEGORIES:
        raise errors.ValidationError(
            detail=f"category '{body.category}' is not a recognized expense category",
            field="category",
        )

    expense = PurchaseExpense(
        tenant_id=user.tenant_id,
        amount=body.amount,
        vendor_name=body.vendor_name,
        expense_date=body.expense_date,
        category=body.category,
        is_capital="true" if body.is_capital else "false",
        description=body.description,
        notes=body.notes,
    )
    db.add(expense)
    db.commit()
    db.refresh(expense)

    return serialize_expense(expense, user)


@router.get("")
def list_expenses(
    user: AuthedUser = Depends(require_owner),
    db: Session = Depends(get_tenant_db_for_user),
    category: Optional[str] = None,
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
) -> List[Dict[str, Any]]:
    """
    List the tenant's expenses, optionally filtered — **Owner only**
    (Req 14.4, 14.5, 14.6).

    :func:`require_owner` rejects Staff with 403 before this runs (Req 14.6).
    When a ``category`` is supplied, only rows in that category are returned.
    When ``start_date`` / ``end_date`` are supplied, the date filter is
    **inclusive** of both endpoints. If both dates are supplied and
    ``start_date`` is later than ``end_date`` the filter is rejected with a 400
    ``validation_error`` naming the ``date`` field and no records are returned;
    the caller's currently displayed records are therefore left unchanged
    (Req 14.5). Results are ordered by expense date descending (most recent
    first).
    """
    if start_date is not None and end_date is not None and start_date > end_date:
        raise errors.ValidationError(
            detail="start_date must be on or before end_date",
            field="date",
        )

    query = db.query(PurchaseExpense).filter(
        PurchaseExpense.tenant_id == user.tenant_id
    )
    if category is not None:
        query = query.filter(PurchaseExpense.category == category)
    if start_date is not None:
        query = query.filter(PurchaseExpense.expense_date >= start_date)
    if end_date is not None:
        query = query.filter(PurchaseExpense.expense_date <= end_date)

    expenses = query.order_by(PurchaseExpense.expense_date.desc()).all()
    return serialize_expenses(expenses, user)
