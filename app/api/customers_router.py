"""
Customers domain router for the app-first REST API (``/api/v1/customers``).

A thin HTTP adapter over the existing, unchanged :class:`CustomerService`
(design §"No business-logic rewrite"). It exposes two endpoints, both available
to **Owner and Staff** (Req 12 carries no financial data, so no role gate beyond
authentication):

- ``POST /api/v1/customers`` — create a customer. The request body is validated
  for a name of 1–100 characters (via :class:`CustomerCreateRequest`) and a
  phone number of exactly 8–15 digits (Req 12.1, 12.4). Per-tenant phone
  uniqueness is enforced by the service; a duplicate is surfaced as a ``409
  conflict`` naming the duplicate phone, with the existing record left unchanged
  (Req 12.3).
- ``GET /api/v1/customers?q=`` — search customers by name (partial,
  case-insensitive) or phone (exact), scoped to the token-derived tenant, with
  the result set **capped at 50** (Req 12.2). An empty/blank term yields an
  empty list.

The tenant is taken exclusively from the authenticated principal via
:func:`get_tenant_db_for_user`, so every query is tenant-scoped by construction
(Req 19.2, 19.6). Service-layer ``ValueError`` (e.g. the duplicate-phone
rejection) is translated to the designed HTTP error bodies by
:func:`errors.map_service_errors`.

_Requirements: 12.1, 12.2, 12.3, 12.4_
"""

from __future__ import annotations

import re
from typing import Any, Dict, List

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.orm import Session

from app.api import errors
from app.api.deps import AuthedUser, get_current_user, get_tenant_db_for_user
from app.api.schemas import (
    CustomerCreateRequest,
    serialize_customer,
    serialize_customers,
)
from app.services.customer_service import CustomerService

# Req 12.2: a search never returns more than 50 customers.
SEARCH_RESULT_CAP = 50

# Req 12.1/12.4: a customer phone number is 8 to 15 digits (no other characters).
_PHONE_PATTERN = re.compile(r"^\d{8,15}$")

router = APIRouter(prefix="/api/v1/customers", tags=["customers"])


@router.post("", status_code=status.HTTP_201_CREATED)
def create_customer(
    body: CustomerCreateRequest,
    user: AuthedUser = Depends(get_current_user),
    db: Session = Depends(get_tenant_db_for_user),
) -> Dict[str, Any]:
    """
    Create a customer for the authenticated user's tenant (Owner+Staff).

    ``CustomerCreateRequest`` already enforces the 1–100 character name. Here we
    additionally require the phone to be 8–15 digits (Req 12.4) before handing
    off to the unchanged service. A phone that already exists for the tenant is
    rejected by the service with a ``ValueError`` that
    :func:`errors.map_service_errors` turns into a ``409 conflict`` naming the
    duplicate phone, leaving the existing record unchanged (Req 12.3).
    """
    if not _PHONE_PATTERN.fullmatch(body.phone):
        raise errors.ValidationError(
            detail="phone number must be 8 to 15 digits",
            field="phone",
        )

    with errors.map_service_errors():
        customer = CustomerService(db).create_customer(
            tenant_id=user.tenant_id,
            name=body.name,
            phone=body.phone,
            address=body.address,
        )

    return serialize_customer(customer, user)


@router.get("")
def search_customers(
    q: str = Query(default="", description="Name (partial) or phone (exact) search term"),
    user: AuthedUser = Depends(get_current_user),
    db: Session = Depends(get_tenant_db_for_user),
) -> List[Dict[str, Any]]:
    """
    Search the tenant's customers by name or phone, capped at 50 (Req 12.2).

    Delegates matching to the unchanged ``CustomerService`` (partial,
    case-insensitive name match or exact phone match), then truncates to
    :data:`SEARCH_RESULT_CAP`. A blank term returns an empty list, which the
    Customers surface renders as an empty-result indication (Req 12.5).
    """
    matches = CustomerService(db).get_customer(user.tenant_id, q)
    capped = matches[:SEARCH_RESULT_CAP]
    return serialize_customers(capped, user)
