"""
Unit tests for the role-aware API schemas / serializers (app-first pivot, task 4.3).

Covers the core guarantee of ``app/api/schemas.py``: serializer functions take
an authenticated principal and **omit cost / cost-per-unit / profit fields from
the serialized payload for Staff**, while Owners see the full payload
(Req 5.4, 10.7, 11.7). Also checks request-model validation and that sale
figures (selling_price, invoice/payment amounts) are retained for Staff.
"""

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError

from app.api import schemas


# ── Duck-typed principal (mirrors deps.AuthedUser) ────────────────────────────

@dataclass
class Principal:
    role: str
    tenant_id: UUID
    user_id: UUID
    device_id: UUID


def owner() -> Principal:
    return Principal(role="owner", tenant_id=uuid4(), user_id=uuid4(), device_id=uuid4())


def staff() -> Principal:
    return Principal(role="staff", tenant_id=uuid4(), user_id=uuid4(), device_id=uuid4())


# ── Lightweight ORM-like stand-ins (attribute access; from_attributes=True) ───

@dataclass
class FakeInventoryItem:
    item_id: UUID
    tenant_id: UUID
    name: str
    category: str
    quantity: Decimal
    unit: str
    cost_per_unit: Decimal
    created_at: datetime
    updated_at: datetime


def make_item() -> FakeInventoryItem:
    now = datetime(2024, 1, 1, 12, 0, 0)
    return FakeInventoryItem(
        item_id=uuid4(),
        tenant_id=uuid4(),
        name="Flour",
        category="ingredient",
        quantity=Decimal("10.00"),
        unit="kg",
        cost_per_unit=Decimal("45.50"),
        created_at=now,
        updated_at=now,
    )


@dataclass
class FakeRecipeCost:
    recipe_name: str
    yield_per_batch: int
    ingredient_cost: Decimal
    packaging_cost: Decimal
    unit_cost: Decimal


# ── Role helpers ──────────────────────────────────────────────────────────────

def test_role_helpers_are_case_insensitive():
    assert schemas.is_staff(Principal("Staff", uuid4(), uuid4(), uuid4())) is True
    assert schemas.is_owner(Principal("OWNER", uuid4(), uuid4(), uuid4())) is True
    assert schemas.is_staff(owner()) is False
    assert schemas.role_of(Principal("", uuid4(), uuid4(), uuid4())) == ""


# ── Inventory cost hiding (Req 10.7) ──────────────────────────────────────────

def test_inventory_owner_sees_cost():
    item = make_item()
    out = schemas.serialize_inventory_item(item, owner())
    assert out["cost_per_unit"] == Decimal("45.50")
    assert out["name"] == "Flour"


def test_inventory_staff_cost_field_absent():
    item = make_item()
    out = schemas.serialize_inventory_item(item, staff())
    # The KEY must be absent, not merely null (Property 3).
    assert "cost_per_unit" not in out
    # Non-cost fields remain.
    assert out["name"] == "Flour"
    assert out["quantity"] == Decimal("10.00")
    assert out["unit"] == "kg"


def test_inventory_list_staff_has_no_cost_anywhere():
    items = [make_item(), make_item()]
    out = schemas.serialize_inventory_items(items, staff())
    assert len(out) == 2
    assert all("cost_per_unit" not in row for row in out)


# ── Recipe cost-per-unit hiding (Req 11.7) ────────────────────────────────────

def test_recipe_cost_owner_sees_all_fields():
    cost = FakeRecipeCost("Brownie", 12, Decimal("100"), Decimal("20"), Decimal("10"))
    out = schemas.serialize_recipe_cost(cost, owner())
    assert out["unit_cost"] == Decimal("10")
    assert out["ingredient_cost"] == Decimal("100")
    assert out["packaging_cost"] == Decimal("20")


def test_recipe_cost_staff_all_cost_fields_absent():
    cost = FakeRecipeCost("Brownie", 12, Decimal("100"), Decimal("20"), Decimal("10"))
    out = schemas.serialize_recipe_cost(cost, staff())
    for hidden in ("unit_cost", "ingredient_cost", "packaging_cost"):
        assert hidden not in out
    # Non-financial descriptors remain.
    assert out["recipe_name"] == "Brownie"
    assert out["yield_per_batch"] == 12


# ── Nested / recursive stripping ──────────────────────────────────────────────

def test_apply_role_visibility_strips_nested_and_lists():
    payload = {
        "name": "R",
        "cost_per_unit": Decimal("5"),
        "components": [
            {"item": "flour", "unit_cost": Decimal("2")},
            {"item": "box", "unit_cost": Decimal("1")},
        ],
        "meta": {"profit": Decimal("9"), "keep": 1},
    }
    out = schemas.apply_role_visibility(payload, staff())
    assert "cost_per_unit" not in out
    assert all("unit_cost" not in c for c in out["components"])
    assert "profit" not in out["meta"]
    assert out["meta"]["keep"] == 1
    # Owner sees everything unchanged.
    assert schemas.apply_role_visibility(payload, owner()) == payload


# ── Sale figures retained for Staff ───────────────────────────────────────────

def test_invoice_amounts_retained_for_staff():
    invoice = schemas.InvoiceResponse(
        invoice_number="INV-1",
        issue_date=date(2024, 1, 1),
        delivery_date=date(2024, 1, 2),
        items=[{"description": "Cake", "quantity": 1,
                "unit_price": Decimal("500"), "total": Decimal("500")}],
        subtotal=Decimal("500"),
        amount_paid=Decimal("200"),
        amount_due=Decimal("300"),
    )
    out = schemas.serialize_invoice(invoice, staff())
    assert out["subtotal"] == Decimal("500")
    assert out["amount_due"] == Decimal("300")
    assert out["items"][0]["total"] == Decimal("500")


# ── Request-model validation ──────────────────────────────────────────────────

def test_inventory_create_request_rejects_out_of_range_cost():
    with pytest.raises(ValidationError):
        schemas.InventoryItemCreateRequest(
            name="Sugar", category="ingredient",
            quantity=Decimal("1"), unit="kg",
            cost_per_unit=Decimal("1000000"),  # > 999,999.99
        )


def test_customer_create_request_rejects_empty_name():
    with pytest.raises(ValidationError):
        schemas.CustomerCreateRequest(name="", phone="12345678")


def test_recipe_create_request_rejects_nonpositive_yield():
    with pytest.raises(ValidationError):
        schemas.RecipeCreateRequest(name="Cookie", yield_per_batch=0)
