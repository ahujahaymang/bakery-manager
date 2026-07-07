"""
Unit tests for the ``inventory`` ingestion doc-type (Req 15.6).

These exercise the additive inventory branch of ``app/api/ingestion_router.py``:
scanning/uploading a purchase receipt whose line items become inventory stock
rows via ``InventoryService.create_item``.

- ``POST /api/v1/ingestion/extract`` (doc_type=inventory) — reuse the receipt
  extractor and shape line items into an inventory draft (Req 15.1).
- ``POST /api/v1/ingestion/confirm`` (doc_type=inventory) — route the (edited)
  draft to inventory creation, persisting nothing on failure (Req 15.6, 15.10).

The environment has no HTTP test client, so tests invoke the route callables
directly with a canned principal and an in-memory SQLite session, mirroring
``tests/test_ingestion_router.py``.
"""

import asyncio
import io
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
from app.models import InventoryItem, Tenant

from app.api import ingestion_router
from app.api.deps import AuthedUser
from app.api.errors import ExtractionFailedError, ValidationError as APIValidationError
from app.api.ingestion_router import IngestionConfirmRequest


TENANT_ID = uuid4()
OWNER = AuthedUser(uuid4(), TENANT_ID, "owner", uuid4())


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
    session.add(Tenant(tenant_id=TENANT_ID, chat_id="test_chat_inventory_ingest"))
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
    """Stub ImageService returning a canned result from the receipt extractor."""

    def __init__(self, result):
        self._result = result

    async def process_receipt_image(self, image_bytes):
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


# ── extract(inventory): shapes receipt items into stock rows (Req 15.1) ─────────

def test_extract_inventory_shapes_items():
    result = {
        "amount": 450.0,
        "method": "Cash",
        "date": "2024-01-05",
        "customer_name": "FreshMart",
        "items": [
            {"name": "Flour", "quantity": 2, "unit": "kg", "price": 100},
            {"name": "Sugar", "quantity": 1, "unit": "kg", "price": 60},
        ],
        "confidence": 0.9,
    }
    resp = _extract("inventory", _png_bytes(), result)

    assert resp.doc_type == "inventory"
    # Only the items list is carried into an inventory draft.
    assert "items" in resp.draft
    assert len(resp.draft["items"]) == 2

    first = resp.draft["items"][0]
    assert first["name"] == "Flour"
    assert first["quantity"] == 2
    assert first["unit"] == "kg"
    # Receipt `price` becomes inventory `cost`; category left blank for the user.
    assert first["cost"] == 100
    assert first["category"] is None


def test_extract_inventory_no_items_maps_to_extraction_failed():
    # A receipt with a total but no line items yields nothing to stock.
    with pytest.raises(ExtractionFailedError):
        _extract("inventory", _png_bytes(), {"amount": 450, "items": []})


# ── confirm(inventory): creates inventory items (Req 15.6) ──────────────────────

def test_confirm_inventory_creates_items(db_session):
    draft = {
        "items": [
            {"name": "Flour", "quantity": 2, "unit": "kg", "cost": 100, "category": "ingredient"},
            {"name": "Boxes", "quantity": 50, "unit": "pcs", "cost": 5, "category": "packaging"},
        ]
    }
    body = _confirm(db_session, "inventory", draft)

    assert len(body["items"]) == 2
    stored = db_session.query(InventoryItem).order_by(InventoryItem.name).all()
    assert len(stored) == 2
    names = {i.name for i in stored}
    assert names == {"Boxes", "Flour"}

    flour = next(i for i in stored if i.name == "Flour")
    assert flour.category == "ingredient"
    assert flour.cost_per_unit == Decimal("100")
    assert flour.quantity == Decimal("2")
    assert flour.unit == "kg"


def test_confirm_inventory_defaults_blank_category_to_ingredient(db_session):
    draft = {"items": [{"name": "Butter", "quantity": 1, "unit": "kg", "cost": 400, "category": ""}]}
    _confirm(db_session, "inventory", draft)

    stored = db_session.query(InventoryItem).filter(InventoryItem.name == "Butter").first()
    assert stored is not None
    assert stored.category == "ingredient"


def test_confirm_inventory_no_items_rejected(db_session):
    with pytest.raises(APIValidationError) as exc:
        _confirm(db_session, "inventory", {"items": []})
    assert exc.value.field == "items"
    assert db_session.query(InventoryItem).count() == 0


# ── confirm(inventory): bad item rejects and persists nothing (Req 15.10) ───────

def test_confirm_inventory_missing_name_rejected_persists_nothing(db_session):
    draft = {
        "items": [
            {"name": "Flour", "quantity": 2, "unit": "kg", "cost": 100},
            {"name": "", "quantity": 1, "unit": "kg", "cost": 60},
        ]
    }
    with pytest.raises(APIValidationError) as exc:
        _confirm(db_session, "inventory", draft)
    assert exc.value.field == "name"
    # Pre-validation runs before any create, so nothing is persisted.
    assert db_session.query(InventoryItem).count() == 0


def test_confirm_inventory_invalid_unit_compensates(db_session):
    # First item is valid and commits; the second has a bad unit that the
    # service rejects mid-batch. The already-created first row must be deleted
    # so nothing from this confirm is persisted (Req 15.10).
    draft = {
        "items": [
            {"name": "Flour", "quantity": 2, "unit": "kg", "cost": 100},
            {"name": "Sugar", "quantity": 1, "unit": "sacks", "cost": 60},
        ]
    }
    with pytest.raises(APIValidationError):
        _confirm(db_session, "inventory", draft)

    assert db_session.query(InventoryItem).count() == 0


def test_confirm_inventory_bad_number_rejected_persists_nothing(db_session):
    draft = {
        "items": [
            {"name": "Flour", "quantity": "not-a-number", "unit": "kg", "cost": 100},
        ]
    }
    with pytest.raises(APIValidationError) as exc:
        _confirm(db_session, "inventory", draft)
    assert exc.value.field == "quantity"
    assert db_session.query(InventoryItem).count() == 0
