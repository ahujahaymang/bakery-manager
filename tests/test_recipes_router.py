"""
Unit tests for the recipes domain router (task 10.1).

These exercise ``app/api/recipes_router.py`` as a thin HTTP adapter over the
unchanged :class:`RecipeService`:

- ``POST   /api/v1/recipes``                — create recipe (Req 11.1, 11.5)
- ``POST   /api/v1/recipes/{id}/components``— add component (Req 11.2, 11.6)
- ``GET    /api/v1/recipes``                — list, cost-free payload (Req 11.7)
- ``GET    /api/v1/recipes/{id}/cost``      — Owner-only cost (Req 11.3, 11.4, 11.7)

The environment has no HTTP test client (httpx) installed, so these tests invoke
the router's route callables directly with a canned principal and an in-memory
SQLite session. This focuses on route → service wiring, role gating, and the
service ``ValueError`` → :class:`APIError` mapping. Token resolution itself is
covered by ``test_api_deps.py``.
"""

from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

# Register all model tables on the shared Base.
import app.models  # noqa: F401
from app.database import Base
from app.models import InventoryItem, Tenant
from app.api import recipes_router
from app.api.deps import AuthedUser, require_owner
from app.api.errors import (
    APIError,
    ConflictError,
    ForbiddenError,
    NotFoundError,
    ValidationError as APIValidationError,
)
from app.api.schemas import RecipeComponentCreateRequest, RecipeCreateRequest
from app.services.recipe_service import RecipeService

from pydantic import ValidationError as PydanticValidationError


TENANT_ID = uuid4()
OWNER = AuthedUser(uuid4(), TENANT_ID, "owner", uuid4())
STAFF = AuthedUser(uuid4(), TENANT_ID, "staff", uuid4())


@pytest.fixture()
def db_session():
    engine = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}
    )

    @event.listens_for(engine, "connect")
    def _fk(conn, _):
        conn.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine)
    session = SessionLocal()
    # Seed the tenant referenced by TENANT_ID so FK constraints hold.
    session.add(Tenant(tenant_id=TENANT_ID, chat_id="test_chat_recipes"))
    session.commit()
    yield session
    session.close()
    Base.metadata.drop_all(engine)
    engine.dispose()


def _seed_item(db, name, cost, unit="g", category="ingredient"):
    item = InventoryItem(
        tenant_id=TENANT_ID,
        name=name,
        category=category,
        quantity=Decimal("100"),
        unit=unit,
        cost_per_unit=Decimal(str(cost)),
    )
    db.add(item)
    db.commit()
    db.refresh(item)
    return item


def _create(db, user, name, yield_per_batch):
    return recipes_router.create_recipe(
        RecipeCreateRequest(name=name, yield_per_batch=yield_per_batch), user, db
    )


def _add(db, user, recipe_id, item_name, quantity, component_type):
    return recipes_router.add_component(
        recipe_id,
        RecipeComponentCreateRequest(
            item_name=item_name, quantity=quantity, component_type=component_type
        ),
        user,
        db,
    )


# ── create recipe (Req 11.1, 11.5) ────────────────────────────────────────────

def test_create_recipe_persists_and_serializes(db_session):
    body = _create(db_session, OWNER, "Brownie", 12)

    assert body["name"] == "Brownie"
    assert body["yield_per_batch"] == 12
    assert "recipe_id" in body
    assert RecipeService(db_session).get_recipe(TENANT_ID, "Brownie") is not None


def test_create_recipe_invalid_yield_rejected_by_request_model(db_session):
    # yield_per_batch must be > 0; the request model rejects it before the route.
    with pytest.raises(PydanticValidationError):
        RecipeCreateRequest(name="Bad", yield_per_batch=0)


def test_create_recipe_duplicate_name_maps_to_conflict(db_session):
    _create(db_session, OWNER, "Cake", 8)
    with pytest.raises(ConflictError) as exc:
        _create(db_session, OWNER, "Cake", 8)
    assert exc.value.status_code == 409


# ── add component (Req 11.2, 11.6) ─────────────────────────────────────────────

def test_add_component_returns_component(db_session):
    _seed_item(db_session, "Flour", cost="2")
    recipe = _create(db_session, OWNER, "Loaf", 4)

    body = _add(db_session, OWNER, recipe["recipe_id"], "Flour", 500, "ingredient")

    assert body["type"] == "ingredient"
    assert body["quantity"] == Decimal("500")


def test_add_component_unknown_recipe_maps_to_404(db_session):
    with pytest.raises(NotFoundError):
        _add(db_session, OWNER, uuid4(), "Flour", 500, "ingredient")


def test_add_component_bad_quantity_rejected_by_request_model(db_session):
    with pytest.raises(PydanticValidationError):
        RecipeComponentCreateRequest(item_name="Flour", quantity=0, component_type="ingredient")


def test_add_component_bad_type_rejected_by_request_model(db_session):
    with pytest.raises(PydanticValidationError):
        RecipeComponentCreateRequest(item_name="Flour", quantity=5, component_type="nonsense")


def test_add_component_unknown_item_maps_to_error(db_session):
    recipe = _create(db_session, OWNER, "Loaf", 4)
    with pytest.raises(APIError) as exc:
        _add(db_session, OWNER, recipe["recipe_id"], "Ghost", 10, "ingredient")
    # Service raises "Inventory item 'Ghost' not found" → mapped to 404.
    assert exc.value.status_code == 404


# ── list recipes (Req 11.7) ────────────────────────────────────────────────────

def test_list_recipes_returns_all_without_cost_fields(db_session):
    _create(db_session, OWNER, "A", 2)
    _create(db_session, OWNER, "B", 3)

    recipes = recipes_router.list_recipes(STAFF, db_session)

    assert {r["name"] for r in recipes} == {"A", "B"}
    for r in recipes:
        assert "cost_per_unit" not in r
        assert "unit_cost" not in r


# ── recipe cost — Owner only (Req 11.3, 11.4, 11.7) ────────────────────────────

def _build_recipe_with_costs(db):
    _seed_item(db, "Flour", cost="2")          # 200 * 2 = 400 ingredient
    _seed_item(db, "Box", cost="5", unit="pcs", category="packaging")  # 1 * 5 = 5
    recipe = _create(db, OWNER, "Cookie", 10)
    rid = recipe["recipe_id"]
    _add(db, OWNER, rid, "Flour", 200, "ingredient")
    _add(db, OWNER, rid, "Box", 1, "packaging")
    return rid


def test_owner_gets_recipe_cost(db_session):
    rid = _build_recipe_with_costs(db_session)

    body = recipes_router.get_recipe_cost(rid, OWNER, db_session)

    assert Decimal(str(body["ingredient_cost"])) == Decimal("400")
    assert Decimal(str(body["packaging_cost"])) == Decimal("5")
    # unit_cost = (400 + 5) / 10 = 40.5
    assert Decimal(str(body["unit_cost"])) == Decimal("40.5")


def test_owner_cost_recalculates_when_inventory_cost_changes(db_session):
    # Req 11.4: cost per unit reflects current inventory unit costs on each read.
    rid = _build_recipe_with_costs(db_session)
    first = recipes_router.get_recipe_cost(rid, OWNER, db_session)
    assert Decimal(str(first["unit_cost"])) == Decimal("40.5")

    flour = RecipeService(db_session).db.query(InventoryItem).filter(
        InventoryItem.name == "Flour"
    ).first()
    flour.cost_per_unit = Decimal("3")  # 200 * 3 = 600 ingredient
    db_session.commit()

    second = recipes_router.get_recipe_cost(rid, OWNER, db_session)
    # unit_cost = (600 + 5) / 10 = 60.5
    assert Decimal(str(second["unit_cost"])) == Decimal("60.5")


def test_staff_recipe_cost_gate_rejects_before_service(db_session):
    # The route depends on require_owner; a Staff principal must be rejected
    # with 403 before any cost value is produced (Req 11.7).
    with pytest.raises(ForbiddenError) as exc:
        require_owner(STAFF)
    assert exc.value.status_code == 403


def test_owner_cost_unknown_recipe_maps_to_404(db_session):
    with pytest.raises(NotFoundError):
        recipes_router.get_recipe_cost(uuid4(), OWNER, db_session)
