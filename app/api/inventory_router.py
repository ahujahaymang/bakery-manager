"""
Inventory domain router — ``/api/v1/inventory/*``.

A thin HTTP adapter over the existing, unchanged :class:`InventoryService`
(design §5, "Domain routers — mapping to existing services"). No business logic
lives here: the router parses/validates the request, opens a tenant-scoped DB
session derived strictly from the authenticated device token, forwards to the
service, and shapes the response with the role-aware serializers in
``app/api/schemas.py``.

Endpoints (all Owner+Staff — the Inventory surface is shared, but cost is hidden
from Staff at serialization, Req 10.7):

- ``POST   /api/v1/inventory``            → :meth:`InventoryService.create_item`
- ``GET    /api/v1/inventory``            → :meth:`InventoryService.list_items`
- ``PATCH  /api/v1/inventory/{item_id}``  → :meth:`InventoryService.update_item`

Validation & error mapping (Req 10.1–10.4): request bodies are parsed by the
Pydantic models in ``schemas.py`` (field lengths and the 0.00–999,999.99 numeric
ranges), and any service-layer ``ValueError`` (empty field, out-of-range value,
missing item) is translated to the designed HTTP error body by
:func:`app.api.errors.map_service_errors` — a 400 ``validation_error`` naming the
offending field, or a 404 ``not_found`` for an unknown item (Req 10.2, 10.4).

Listing (Req 10.5): :func:`serialize_inventory_list` groups items by category
with **categories ordered alphabetically ascending** and **items within each
category ordered alphabetically ascending by name** (case-insensitive). Empty
category groups are omitted so the payload carries only categories that have
items (supports the surface's empty-state, Req 10.6).

Role-aware cost hiding (Req 10.7): every item is serialized through
:func:`serialize_inventory_item` / :func:`serialize_inventory_items`, which strip
``cost_per_unit`` from the payload entirely when the principal holds the Staff
role. This is defense-in-depth on top of the shared-endpoint access model.

_Requirements: 10.1, 10.2, 10.3, 10.4, 10.5, 10.7_
"""

from __future__ import annotations

from typing import Any, Dict, List
from uuid import UUID

from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from app.api import errors
from app.api.deps import AuthedUser, get_current_user, get_tenant_db_for_user
from app.api.schemas import (
    AuthedUserLike,
    InventoryItemCreateRequest,
    InventoryItemUpdateRequest,
    serialize_inventory_item,
    serialize_inventory_items,
)
from app.services.inventory_service import InventoryService

router = APIRouter(prefix="/api/v1/inventory", tags=["inventory"])


def serialize_inventory_list(
    grouped: Dict[str, List[Any]],
    user: "AuthedUserLike",
) -> Dict[str, List[Dict[str, Any]]]:
    """
    Shape :meth:`InventoryService.list_items` output for the Inventory surface.

    The service returns items grouped by category. This serializer enforces the
    display contract of Req 10.5:

    - **Categories** are emitted in ascending alphabetical order.
    - **Items** within each category are emitted in ascending alphabetical order
      by name (case-insensitive, so "Almond" and "almond" sort naturally).
    - Empty category groups are dropped, so the payload lists only categories
      that actually contain items (Req 10.6 empty-state).

    Each item is serialized via :func:`serialize_inventory_items`, so a Staff
    principal's payload never carries ``cost_per_unit`` (Req 10.7).

    Returns ``{"categories": [{"category": str, "items": [...]}, ...]}``. When no
    items exist the ``categories`` list is empty.
    """
    categories: List[Dict[str, Any]] = []
    for category in sorted(grouped.keys()):
        items = grouped[category]
        if not items:
            continue
        ordered = sorted(items, key=lambda item: (item.name or "").lower())
        categories.append(
            {
                "category": category,
                "items": serialize_inventory_items(ordered, user),
            }
        )
    return {"categories": categories}


@router.post("", status_code=status.HTTP_201_CREATED)
def create_inventory_item(
    body: InventoryItemCreateRequest,
    user: AuthedUser = Depends(get_current_user),
    db: Session = Depends(get_tenant_db_for_user),
) -> Dict[str, Any]:
    """
    Create a new inventory item for the acting tenant (Req 10.1, 10.2).

    Owner+Staff. The request body's field lengths and 0.00–999,999.99 numeric
    ranges are enforced by :class:`InventoryItemCreateRequest`; the service
    performs its own field/range validation and raises ``ValueError`` for an
    empty or out-of-range field, which :func:`map_service_errors` turns into a
    400 ``validation_error`` naming the field (Req 10.2). On success the
    persisted item — including its assigned identifier — is returned, with
    ``cost_per_unit`` hidden for Staff (Req 10.7).
    """
    service = InventoryService(db)
    with errors.map_service_errors():
        item = service.create_item(
            tenant_id=user.tenant_id,
            name=body.name,
            category=body.category,
            quantity=body.quantity,
            unit=body.unit,
            cost_per_unit=body.cost_per_unit,
        )
    return serialize_inventory_item(item, user)


@router.get("")
def list_inventory_items(
    user: AuthedUser = Depends(get_current_user),
    db: Session = Depends(get_tenant_db_for_user),
) -> Dict[str, List[Dict[str, Any]]]:
    """
    List the tenant's inventory items grouped and alphabetically ordered.

    Owner+Staff. Delegates to :meth:`InventoryService.list_items` and shapes the
    result via :func:`serialize_inventory_list`: categories ascending, items
    ascending by name, empty groups omitted (Req 10.5, 10.6). Staff payloads
    carry no ``cost_per_unit`` (Req 10.7).
    """
    service = InventoryService(db)
    grouped = service.list_items(tenant_id=user.tenant_id)
    return serialize_inventory_list(grouped, user)


@router.patch("/{item_id}")
def update_inventory_item(
    item_id: UUID,
    body: InventoryItemUpdateRequest,
    user: AuthedUser = Depends(get_current_user),
    db: Session = Depends(get_tenant_db_for_user),
) -> Dict[str, Any]:
    """
    Update an existing inventory item's quantity and/or cost (Req 10.3, 10.4).

    Owner+Staff. The item is resolved by ``item_id`` within the acting tenant;
    an unknown id yields a 404 ``not_found`` and no value is changed (Req 10.4).
    Only the fields present in the body (``quantity`` and/or ``cost_per_unit``)
    are forwarded to :meth:`InventoryService.update_item`; the Pydantic model
    enforces the 0.00–999,999.99 ranges and the service re-validates and raises
    ``ValueError`` for an out-of-range value, mapped to a 400 ``validation_error``
    while the previously persisted value is retained (Req 10.4). The updated item
    is returned with ``cost_per_unit`` hidden for Staff (Req 10.7).
    """
    service = InventoryService(db)

    existing = service.get_item_by_id(tenant_id=user.tenant_id, item_id=item_id)
    if existing is None:
        raise errors.NotFoundError()

    updates = body.model_dump(exclude_unset=True, exclude_none=True)

    with errors.map_service_errors():
        item = service.update_item(
            tenant_id=user.tenant_id,
            name=existing.name,
            updates=updates,
        )
    return serialize_inventory_item(item, user)
