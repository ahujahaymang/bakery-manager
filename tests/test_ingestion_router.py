"""
Unit tests for the image ingestion router (task 15.1).

These exercise ``app/api/ingestion_router.py`` as a thin HTTP adapter over the
unchanged ``ImageService`` (extraction) and the domain services (confirm):

- ``POST /api/v1/ingestion/extract``  — validate + extract → typed draft
  (Req 15.1, 15.8, 15.9)
- ``POST /api/v1/ingestion/confirm``  — route draft → domain create, persist
  nothing on failure (Req 15.4, 15.5, 15.6, 15.10)

The environment has no HTTP test client, so tests invoke the route callables
directly with a canned principal and an in-memory SQLite session. The async
``extract`` route is driven with ``asyncio.run`` and a stub image service.
"""

import asyncio
import io
from datetime import date, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
from PIL import Image
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from starlette.datastructures import UploadFile

# Register all model tables on the shared Base.
import app.models  # noqa: F401
from app.database import Base
from app.models import Customer, InventoryItem, PurchaseExpense, Product, Recipe, Tenant

from app.api import ingestion_router
from app.api.deps import AuthedUser
from app.api.errors import ExtractionFailedError, ValidationError as APIValidationError
from app.api.ingestion_router import IngestionConfirmRequest


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
    session.add(Tenant(tenant_id=TENANT_ID, chat_id="test_chat_ingestion"))
    session.commit()
    yield session
    session.close()
    Base.metadata.drop_all(engine)
    engine.dispose()


# ── Helpers ─────────────────────────────────────────────────────────────────

def _png_bytes(size=(10, 10), fmt="PNG") -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, (200, 120, 60)).save(buf, format=fmt)
    return buf.getvalue()


def _upload(data: bytes, filename="receipt.png") -> UploadFile:
    return UploadFile(file=io.BytesIO(data), filename=filename)


class _StubImageService:
    """Stub ImageService returning a canned result per method."""

    def __init__(self, result):
        self._result = result

    async def process_receipt_image(self, image_bytes):
        return self._result

    async def process_recipe_image(self, image_bytes):
        return self._result

    async def process_order_image(self, image_bytes):
        return self._result

    async def process_catalog_image(self, image_bytes):
        return self._result


def _extract(doc_type, data, result):
    return asyncio.run(
        ingestion_router.extract(
            doc_type=doc_type,
            image=_upload(data),
            user=OWNER,
            image_service=_StubImageService(result),
        )
    )


def _confirm(db, doc_type, draft, user=OWNER):
    return ingestion_router.confirm(
        IngestionConfirmRequest(doc_type=doc_type, draft=draft), user, db
    )


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
    return item


def _seed_customer(db, name="Asha", phone="9990001111"):
    c = Customer(tenant_id=TENANT_ID, name=name, phone=phone)
    db.add(c)
    db.commit()
    db.refresh(c)
    return c


# ── extract: validation (Req 15.8) ─────────────────────────────────────────────

def test_extract_rejects_unsupported_doc_type():
    with pytest.raises(APIValidationError) as exc:
        _extract("invoice", _png_bytes(), {"amount": 100})
    assert exc.value.field == "doc_type"
    assert exc.value.status_code == 400


def test_extract_rejects_oversized_image():
    big = b"\x89PNG\r\n" + b"0" * (ingestion_router.MAX_IMAGE_BYTES + 1)
    with pytest.raises(APIValidationError) as exc:
        _extract("receipt", big, {"amount": 100})
    assert exc.value.field == "file"


def test_extract_rejects_non_image_bytes():
    with pytest.raises(APIValidationError) as exc:
        _extract("receipt", b"this is not an image", {"amount": 100})
    assert exc.value.field == "file"


def test_extract_rejects_empty_upload():
    with pytest.raises(APIValidationError):
        _extract("receipt", b"", {"amount": 100})


# ── extract: success + failure shaping (Req 15.1, 15.9) ─────────────────────────

def test_extract_receipt_returns_typed_draft():
    result = {
        "amount": 450.0,
        "method": "Cash",
        "date": "2024-01-05",
        "customer_name": "FreshMart",
        "items": [{"name": "Flour", "quantity": 2, "unit": "kg", "price": 100}],
        "confidence": 0.9,
        "raw_text": "FreshMart ... total 450",
    }
    resp = _extract("receipt", _png_bytes(), result)
    assert resp.doc_type == "receipt"
    assert resp.confidence == pytest.approx(0.9)
    assert resp.draft["amount"] == 450.0
    assert resp.draft["items"][0]["name"] == "Flour"


def test_extract_payment_uses_receipt_extractor():
    result = {"amount": 300, "method": "UPI", "customer_name": "Asha", "confidence": 0.8}
    resp = _extract("payment", _png_bytes(), result)
    assert resp.doc_type == "payment"
    assert resp.draft["amount"] == 300


def test_extract_catalog_returns_categories():
    result = {
        "categories": [
            {
                "name": "Cookies",
                "products": [
                    {"name": "Choc Chip", "variants": [{"size_label": "250g", "price": 200}]}
                ],
            }
        ],
        "confidence": 0.7,
    }
    resp = _extract("catalog", _png_bytes(), result)
    assert resp.draft["categories"][0]["products"][0]["name"] == "Choc Chip"


def test_extract_error_result_maps_to_extraction_failed():
    with pytest.raises(ExtractionFailedError) as exc:
        _extract("receipt", _png_bytes(), {"error": "vision api down"})
    assert exc.value.status_code == 422


def test_extract_empty_result_maps_to_extraction_failed():
    # No amount, no items, no customer → nothing extracted.
    with pytest.raises(ExtractionFailedError):
        _extract("receipt", _png_bytes(), {"amount": None, "items": [], "customer_name": None})


def test_extract_empty_catalog_maps_to_extraction_failed():
    with pytest.raises(ExtractionFailedError):
        _extract("catalog", _png_bytes(), {"categories": []})


# ── confirm: receipt → expense (Req 15.4, 15.6) ─────────────────────────────────

def test_confirm_receipt_creates_expense(db_session):
    draft = {
        "amount": 450,
        "category": "ingredients",
        "date": "2024-01-05",
        "vendor_name": "FreshMart",
        "description": "flour 10kg",
    }
    body = _confirm(db_session, "receipt", draft)
    assert Decimal(str(body["amount"])) == Decimal("450")
    assert body["category"] == "ingredients"

    stored = db_session.query(PurchaseExpense).all()
    assert len(stored) == 1
    assert stored[0].vendor_name == "FreshMart"


def test_confirm_receipt_missing_amount_rejected_persists_nothing(db_session):
    with pytest.raises(APIValidationError) as exc:
        _confirm(db_session, "receipt", {"category": "ingredients"})
    assert exc.value.field == "amount"
    assert db_session.query(PurchaseExpense).count() == 0


def test_confirm_receipt_invalid_category_rejected(db_session):
    with pytest.raises(APIValidationError) as exc:
        _confirm(db_session, "receipt", {"amount": 100, "category": "nonsense"})
    assert exc.value.field == "category"
    assert db_session.query(PurchaseExpense).count() == 0


def test_confirm_receipt_defaults_category_and_date(db_session):
    body = _confirm(db_session, "receipt", {"amount": 99})
    assert body["category"] == "other"
    stored = db_session.query(PurchaseExpense).first()
    assert stored.expense_date == date.today()


# ── confirm: recipe → recipe + components (Req 15.6, 15.10) ──────────────────────

def test_confirm_recipe_creates_recipe_and_components(db_session):
    _seed_item(db_session, "Flour", cost="2")
    _seed_item(db_session, "Box", cost="5", unit="pcs", category="packaging")
    draft = {
        "name": "Brownie",
        "yield_per_batch": 12,
        "ingredients": [{"item_name": "Flour", "quantity": 500}],
        "packaging": [{"item_name": "Box", "quantity": 1}],
    }
    body = _confirm(db_session, "recipe", draft)
    assert body["name"] == "Brownie"

    recipe = db_session.query(Recipe).filter(Recipe.name == "Brownie").first()
    assert recipe is not None
    from app.models import RecipeComponent

    comps = db_session.query(RecipeComponent).filter(
        RecipeComponent.recipe_id == recipe.recipe_id
    ).all()
    assert len(comps) == 2


def test_confirm_recipe_component_failure_compensates(db_session):
    # "Ghost" inventory item does not exist → add_component fails.
    draft = {
        "name": "Failer",
        "yield_per_batch": 4,
        "ingredients": [{"item_name": "Ghost", "quantity": 10}],
    }
    with pytest.raises(Exception):
        _confirm(db_session, "recipe", draft)

    # Req 15.10: nothing persisted — the created recipe was rolled back.
    assert db_session.query(Recipe).filter(Recipe.name == "Failer").count() == 0


# ── confirm: order → OrderService.create_order (Req 15.6) ────────────────────────

def test_confirm_order_creates_order(db_session):
    _seed_customer(db_session, name="Asha", phone="9990001111")
    future = (date.today() + timedelta(days=3)).isoformat()
    draft = {
        "customer_phone": "9990001111",
        "delivery_date": future,
        "items": [{"recipe_name": "Cake", "quantity": 2, "selling_price": 500}],
    }
    body = _confirm(db_session, "order", draft)
    assert body["status"] == "pending"
    assert len(body["items"]) == 1


def test_confirm_order_missing_customer_rejected(db_session):
    with pytest.raises(APIValidationError) as exc:
        _confirm(db_session, "order", {"delivery_date": date.today().isoformat(), "items": []})
    assert exc.value.field == "customer"


def test_confirm_order_missing_delivery_date_rejected(db_session):
    _seed_customer(db_session, phone="9990001111")
    with pytest.raises(APIValidationError) as exc:
        _confirm(
            db_session,
            "order",
            {"customer_phone": "9990001111", "items": [{"recipe_name": "X", "quantity": 1, "selling_price": 10}]},
        )
    assert exc.value.field == "delivery_date"


# ── confirm: catalog → ProductService.create_product (Req 15.6, 15.10) ───────────

def test_confirm_catalog_creates_products(db_session):
    draft = {
        "categories": [
            {
                "name": "Cookies",
                "products": [
                    {"name": "Choc Chip", "variants": [{"size_label": "250g", "price": 200}]},
                    {"name": "Oatmeal", "variants": [{"size_label": "250g", "price": 180}]},
                ],
            }
        ]
    }
    body = _confirm(db_session, "catalog", draft)
    assert len(body["products"]) == 2
    assert db_session.query(Product).count() == 2


def test_confirm_catalog_no_products_rejected(db_session):
    with pytest.raises(APIValidationError) as exc:
        _confirm(db_session, "catalog", {"categories": []})
    assert exc.value.field == "products"


def test_confirm_catalog_product_without_variant_rejected(db_session):
    draft = {"categories": [{"name": "C", "products": [{"name": "NoPrice", "variants": []}]}]}
    with pytest.raises(APIValidationError) as exc:
        _confirm(db_session, "catalog", draft)
    assert exc.value.field == "variants"
    assert db_session.query(Product).count() == 0


def test_confirm_catalog_duplicate_compensates(db_session):
    # First product valid, second duplicates the first → create fails on the
    # second, and the first must be rolled back (Req 15.10).
    draft = {
        "categories": [
            {
                "name": "C",
                "products": [
                    {"name": "Same", "variants": [{"size_label": "1", "price": 10}]},
                    {"name": "Same", "variants": [{"size_label": "2", "price": 20}]},
                ],
            }
        ]
    }
    with pytest.raises(Exception):
        _confirm(db_session, "catalog", draft)
    assert db_session.query(Product).count() == 0


# ── confirm: payment → PaymentService.record_payment (Req 15.6) ──────────────────

def test_confirm_payment_missing_identifier_rejected(db_session):
    with pytest.raises(APIValidationError) as exc:
        _confirm(db_session, "payment", {"amount": 100, "method": "Cash"})
    assert exc.value.field == "order_identifier"


# ── confirm: unsupported doc type ────────────────────────────────────────────────

def test_confirm_unsupported_doc_type_rejected(db_session):
    with pytest.raises(APIValidationError) as exc:
        _confirm(db_session, "invoice", {})
    assert exc.value.field == "doc_type"
