"""Tests for RecipeService."""

import pytest
from decimal import Decimal

from app.services.recipe_service import RecipeService
from app.services.inventory_service import InventoryService


def _add_ingredient(db, tenant_id, name="Flour", qty=Decimal("5"), unit="kg", cost=Decimal("40")):
    return InventoryService(db).create_item(tenant_id, name, "ingredient", qty, unit, cost)


class TestCreateRecipe:
    def test_creates_recipe(self, db, tenant_id):
        svc = RecipeService(db)
        recipe = svc.create_recipe(tenant_id, "Brownies", 12)
        assert recipe.name == "Brownies"
        assert recipe.yield_per_batch == 12

    def test_rejects_duplicate_name(self, db, tenant_id):
        svc = RecipeService(db)
        svc.create_recipe(tenant_id, "Brownies", 12)
        with pytest.raises(ValueError, match="already exists"):
            svc.create_recipe(tenant_id, "brownies", 6)

    def test_rejects_zero_yield(self, db, tenant_id):
        svc = RecipeService(db)
        with pytest.raises(ValueError):
            svc.create_recipe(tenant_id, "Brownies", 0)

    def test_rejects_empty_name(self, db, tenant_id):
        svc = RecipeService(db)
        with pytest.raises(ValueError):
            svc.create_recipe(tenant_id, "", 12)


class TestGetRecipe:
    def test_finds_case_insensitive(self, db, tenant_id):
        svc = RecipeService(db)
        svc.create_recipe(tenant_id, "Brownies", 12)
        assert svc.get_recipe(tenant_id, "brownies") is not None
        assert svc.get_recipe(tenant_id, "BROWNIES") is not None

    def test_returns_none_for_missing(self, db, tenant_id):
        svc = RecipeService(db)
        assert svc.get_recipe(tenant_id, "NonExistent") is None


class TestAddComponent:
    def test_adds_ingredient(self, db, tenant_id):
        svc = RecipeService(db)
        svc.create_recipe(tenant_id, "Brownies", 12)
        _add_ingredient(db, tenant_id, "Flour")
        component = svc.add_component(tenant_id, "Brownies", "Flour", Decimal("200"), "ingredient")
        assert component.quantity == Decimal("200")
        assert component.type == "ingredient"

    def test_rejects_missing_recipe(self, db, tenant_id):
        svc = RecipeService(db)
        _add_ingredient(db, tenant_id, "Flour")
        with pytest.raises(ValueError, match="not found"):
            svc.add_component(tenant_id, "NonExistent", "Flour", Decimal("200"), "ingredient")

    def test_rejects_missing_inventory_item(self, db, tenant_id):
        svc = RecipeService(db)
        svc.create_recipe(tenant_id, "Brownies", 12)
        with pytest.raises(ValueError, match="not found"):
            svc.add_component(tenant_id, "Brownies", "NonExistent", Decimal("200"), "ingredient")

    def test_rejects_invalid_component_type(self, db, tenant_id):
        svc = RecipeService(db)
        svc.create_recipe(tenant_id, "Brownies", 12)
        _add_ingredient(db, tenant_id, "Flour")
        with pytest.raises(ValueError, match="Invalid component type"):
            svc.add_component(tenant_id, "Brownies", "Flour", Decimal("200"), "invalid")


class TestCalculateCost:
    def test_calculates_ingredient_cost(self, db, tenant_id):
        svc = RecipeService(db)
        svc.create_recipe(tenant_id, "Brownies", 10)
        _add_ingredient(db, tenant_id, "Flour", cost=Decimal("40"))  # 40/kg
        svc.add_component(tenant_id, "Brownies", "Flour", Decimal("0.5"), "ingredient")  # 0.5kg

        cost = svc.calculate_cost(tenant_id, "Brownies")
        assert cost.ingredient_cost == Decimal("20")  # 0.5 * 40
        assert cost.unit_cost == Decimal("2")          # 20 / 10 yield

    def test_zero_cost_with_no_components(self, db, tenant_id):
        svc = RecipeService(db)
        svc.create_recipe(tenant_id, "Brownies", 10)
        cost = svc.calculate_cost(tenant_id, "Brownies")
        assert cost.ingredient_cost == Decimal("0")
        assert cost.packaging_cost == Decimal("0")


class TestUpdateRecipe:
    def test_renames_recipe(self, db, tenant_id):
        svc = RecipeService(db)
        svc.create_recipe(tenant_id, "Brownies", 12)
        updated = svc.update_recipe(tenant_id, "Brownies", new_name="Chocolate Brownies")
        assert updated.name == "Chocolate Brownies"

    def test_updates_yield(self, db, tenant_id):
        svc = RecipeService(db)
        svc.create_recipe(tenant_id, "Brownies", 12)
        updated = svc.update_recipe(tenant_id, "Brownies", new_yield=24)
        assert updated.yield_per_batch == 24

    def test_rejects_rename_to_existing(self, db, tenant_id):
        svc = RecipeService(db)
        svc.create_recipe(tenant_id, "Brownies", 12)
        svc.create_recipe(tenant_id, "Cookies", 24)
        with pytest.raises(ValueError, match="already exists"):
            svc.update_recipe(tenant_id, "Brownies", new_name="Cookies")


class TestDeleteRecipe:
    def test_deletes_recipe_and_components(self, db, tenant_id):
        svc = RecipeService(db)
        svc.create_recipe(tenant_id, "Brownies", 12)
        _add_ingredient(db, tenant_id, "Flour")
        svc.add_component(tenant_id, "Brownies", "Flour", Decimal("200"), "ingredient")

        svc.delete_recipe(tenant_id, "Brownies")
        assert svc.get_recipe(tenant_id, "Brownies") is None

    def test_raises_for_missing_recipe(self, db, tenant_id):
        svc = RecipeService(db)
        with pytest.raises(ValueError, match="not found"):
            svc.delete_recipe(tenant_id, "NonExistent")
