"""Tests for InventoryService."""

import pytest
from decimal import Decimal
from uuid import UUID

from app.services.inventory_service import InventoryService


class TestCreateItem:
    def test_creates_item_successfully(self, db, tenant_id):
        svc = InventoryService(db)
        item = svc.create_item(tenant_id, "Flour", "ingredient", Decimal("5"), "kg", Decimal("40"))
        assert item.name == "Flour"
        assert item.category == "ingredient"
        assert item.quantity == Decimal("5")
        assert item.unit == "kg"
        assert item.cost_per_unit == Decimal("40")

    def test_normalises_name_and_category_case(self, db, tenant_id):
        svc = InventoryService(db)
        item = svc.create_item(tenant_id, "  SUGAR  ", "INGREDIENT", Decimal("2"), "kg", Decimal("30"))
        assert item.name == "SUGAR"   # name preserved as-is
        assert item.category == "ingredient"  # category lowercased

    def test_rejects_duplicate_name(self, db, tenant_id):
        svc = InventoryService(db)
        svc.create_item(tenant_id, "Flour", "ingredient", Decimal("5"), "kg", Decimal("40"))
        with pytest.raises(ValueError, match="already exists"):
            svc.create_item(tenant_id, "flour", "ingredient", Decimal("3"), "kg", Decimal("35"))

    def test_rejects_invalid_category(self, db, tenant_id):
        svc = InventoryService(db)
        with pytest.raises(ValueError, match="Invalid category"):
            svc.create_item(tenant_id, "Box", "container", Decimal("10"), "pcs", Decimal("5"))

    def test_rejects_invalid_unit(self, db, tenant_id):
        svc = InventoryService(db)
        with pytest.raises(ValueError, match="Invalid unit"):
            svc.create_item(tenant_id, "Flour", "ingredient", Decimal("5"), "pounds", Decimal("40"))

    def test_rejects_zero_quantity(self, db, tenant_id):
        svc = InventoryService(db)
        with pytest.raises(ValueError, match="positive"):
            svc.create_item(tenant_id, "Flour", "ingredient", Decimal("0"), "kg", Decimal("40"))

    def test_rejects_zero_cost(self, db, tenant_id):
        svc = InventoryService(db)
        with pytest.raises(ValueError, match="positive"):
            svc.create_item(tenant_id, "Flour", "ingredient", Decimal("5"), "kg", Decimal("0"))

    def test_tenant_isolation(self, db, tenant_id):
        from app.models import Tenant
        other = Tenant(chat_id="other_chat")
        db.add(other)
        db.commit()

        svc = InventoryService(db)
        svc.create_item(tenant_id, "Flour", "ingredient", Decimal("5"), "kg", Decimal("40"))
        # Same name is allowed for a different tenant
        item = svc.create_item(other.tenant_id, "Flour", "ingredient", Decimal("3"), "kg", Decimal("35"))
        assert item.tenant_id == other.tenant_id


class TestGetItem:
    def test_finds_item_case_insensitive(self, db, tenant_id):
        svc = InventoryService(db)
        svc.create_item(tenant_id, "Flour", "ingredient", Decimal("5"), "kg", Decimal("40"))
        assert svc.get_item(tenant_id, "flour") is not None
        assert svc.get_item(tenant_id, "FLOUR") is not None

    def test_returns_none_for_missing_item(self, db, tenant_id):
        svc = InventoryService(db)
        assert svc.get_item(tenant_id, "NonExistent") is None


class TestUpdateItem:
    def test_updates_quantity(self, db, tenant_id):
        svc = InventoryService(db)
        svc.create_item(tenant_id, "Flour", "ingredient", Decimal("5"), "kg", Decimal("40"))
        updated = svc.update_item(tenant_id, "Flour", {"quantity": Decimal("10")})
        assert updated.quantity == Decimal("10")

    def test_updates_cost(self, db, tenant_id):
        svc = InventoryService(db)
        svc.create_item(tenant_id, "Flour", "ingredient", Decimal("5"), "kg", Decimal("40"))
        updated = svc.update_item(tenant_id, "Flour", {"cost_per_unit": Decimal("45")})
        assert updated.cost_per_unit == Decimal("45")

    def test_raises_for_missing_item(self, db, tenant_id):
        svc = InventoryService(db)
        with pytest.raises(ValueError, match="not found"):
            svc.update_item(tenant_id, "NonExistent", {"quantity": Decimal("5")})


class TestListItems:
    def test_groups_by_category(self, db, tenant_id):
        svc = InventoryService(db)
        svc.create_item(tenant_id, "Flour", "ingredient", Decimal("5"), "kg", Decimal("40"))
        svc.create_item(tenant_id, "Box", "packaging", Decimal("100"), "pcs", Decimal("2"))

        grouped = svc.list_items(tenant_id)
        assert len(grouped["ingredient"]) == 1
        assert len(grouped["packaging"]) == 1

    def test_empty_when_no_items(self, db, tenant_id):
        svc = InventoryService(db)
        grouped = svc.list_items(tenant_id)
        assert grouped["ingredient"] == []
        assert grouped["packaging"] == []
