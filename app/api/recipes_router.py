"""
Recipes domain router for the app-first REST API (``/api/v1/recipes``).

A thin HTTP adapter over the existing, unchanged :class:`RecipeService`
(design §"Domain routers — mapping to existing services", Recipes rows). It
maps four routes onto service calls and applies role-aware serialization:

| Route                                    | Service call                    | Access        |
|------------------------------------------|---------------------------------|---------------|
| ``POST   /api/v1/recipes``               | ``RecipeService.create_recipe`` | Owner + Staff |
| ``POST   /api/v1/recipes/{id}/components``| ``RecipeService.add_component`` | Owner + Staff |
| ``GET    /api/v1/recipes``               | ``RecipeService.list_recipes``  | Owner + Staff |
| ``GET    /api/v1/recipes/{id}/cost``     | ``RecipeService.calculate_cost``| **Owner only**|

Behavior notes:

- The tenant is always taken from the authenticated principal
  (:func:`get_tenant_db_for_user` / :func:`get_current_user`), never from
  request input, so every query is tenant-scoped by construction (Req 19.2).
- Validation ranges are enforced first by the Pydantic request models and again
  by the service; service ``ValueError``\\s are translated to the designed HTTP
  bodies by :func:`errors.map_service_errors` (400/404/409) — Req 11.5, 11.6.
- ``RecipeService`` addresses recipes by name, so the ``{id}``-scoped routes
  resolve the recipe by id under the caller's tenant first (404 if absent) and
  forward its name to the service.
- **Cost is Owner-only.** ``GET /{id}/cost`` is gated by :func:`require_owner`,
  which rejects Staff with 403 *before* the service runs; the cost serializer
  additionally strips cost fields as defense-in-depth (Req 11.7). The recipe
  list carries no cost-per-unit field, so Staff never receive cost data there.

_Requirements: 11.1, 11.2, 11.3, 11.4, 11.5, 11.6, 11.7_
"""

from __future__ import annotations

from typing import Any, Dict, List
from uuid import UUID

from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from app.api import errors
from app.api.deps import (
    AuthedUser,
    get_current_user,
    get_tenant_db_for_user,
    require_owner,
)
from app.api.schemas import (
    RecipeComponentCreateRequest,
    RecipeCreateRequest,
    serialize_recipe,
    serialize_recipe_cost,
    serialize_recipes,
)
from app.services.recipe_service import RecipeService

router = APIRouter(prefix="/api/v1/recipes", tags=["recipes"])


def _get_recipe_or_404(service: RecipeService, tenant_id: UUID, recipe_id: UUID):
    """Resolve a recipe by id under the caller's tenant or raise 404 (Req 19.2)."""
    recipe = service.get_recipe_by_id(tenant_id, recipe_id)
    if recipe is None:
        raise errors.NotFoundError()
    return recipe


@router.post("", status_code=status.HTTP_201_CREATED)
def create_recipe(
    body: RecipeCreateRequest,
    user: AuthedUser = Depends(get_current_user),
    db: Session = Depends(get_tenant_db_for_user),
) -> Dict[str, Any]:
    """
    Create a recipe for the caller's tenant (Req 11.1, 11.5).

    Name/yield ranges are validated by the request model and re-validated by the
    service; an invalid field or a duplicate name surfaces as the designed
    400/409 body via :func:`errors.map_service_errors`.
    """
    service = RecipeService(db)
    with errors.map_service_errors():
        recipe = service.create_recipe(
            tenant_id=user.tenant_id,
            name=body.name,
            yield_per_batch=body.yield_per_batch,
        )
    return serialize_recipe(recipe, user)


@router.post("/{recipe_id}/components", status_code=status.HTTP_201_CREATED)
def add_component(
    recipe_id: UUID,
    body: RecipeComponentCreateRequest,
    user: AuthedUser = Depends(get_current_user),
    db: Session = Depends(get_tenant_db_for_user),
) -> Dict[str, Any]:
    """
    Attach an inventory item to a recipe as an ingredient/packaging component
    (Req 11.2, 11.6).

    The recipe is resolved by id under the caller's tenant (404 if absent); its
    name is forwarded to :meth:`RecipeService.add_component`. Quantity range and
    the existence of the inventory item are validated by the service.
    """
    service = RecipeService(db)
    with errors.map_service_errors():
        recipe = _get_recipe_or_404(service, user.tenant_id, recipe_id)
        component = service.add_component(
            tenant_id=user.tenant_id,
            recipe_name=recipe.name,
            item_name=body.item_name,
            quantity=body.quantity,
            component_type=body.component_type,
        )
    return {
        "component_id": component.component_id,
        "recipe_id": component.recipe_id,
        "item_id": component.item_id,
        "quantity": component.quantity,
        "type": component.type,
    }


@router.get("")
def list_recipes(
    user: AuthedUser = Depends(get_current_user),
    db: Session = Depends(get_tenant_db_for_user),
) -> List[Dict[str, Any]]:
    """
    List the caller's recipes (Owner + Staff).

    The recipe payload carries no cost-per-unit field, so Staff never receive
    cost data here; the serializer additionally strips any financial keys as
    defense-in-depth (Req 11.7).
    """
    service = RecipeService(db)
    recipes = service.list_recipes(user.tenant_id)
    return serialize_recipes(recipes, user)


@router.get("/{recipe_id}/cost")
def get_recipe_cost(
    recipe_id: UUID,
    user: AuthedUser = Depends(require_owner),
    db: Session = Depends(get_tenant_db_for_user),
) -> Dict[str, Any]:
    """
    Return a recipe's computed cost-per-unit breakdown — **Owner only**
    (Req 11.3, 11.4, 11.7).

    :func:`require_owner` rejects Staff with 403 before this runs, so no
    cost-per-unit value is ever returned to Staff. The recipe is resolved by id
    under the caller's tenant (404 if absent) and its name forwarded to
    :meth:`RecipeService.calculate_cost`, which recomputes from current
    inventory unit costs on every call (Req 11.4).
    """
    service = RecipeService(db)
    with errors.map_service_errors():
        recipe = _get_recipe_or_404(service, user.tenant_id, recipe_id)
        cost = service.calculate_cost(
            tenant_id=user.tenant_id,
            recipe_name=recipe.name,
        )
    return serialize_recipe_cost(cost, user)
