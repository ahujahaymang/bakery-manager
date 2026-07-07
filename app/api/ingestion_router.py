"""
Image ingestion router for the app-first REST API (``/api/v1/ingestion``).

Implements the **confirm-and-edit, no-chat** ingestion flow (design §7). It is a
thin HTTP adapter over the **existing, unchanged** ``ImageService`` (vision
extraction) and the existing domain services used to persist a confirmed draft.
No business logic lives here — the router only validates input, shapes a typed
``Ingestion_Draft``, and routes a confirmed draft to the matching domain create.

Two endpoints, both Owner + Staff (they depend on :func:`get_current_user`, not
:func:`require_owner`), tenant-scoped strictly from the device session token
(Req 19.2, 19.6):

- ``POST /api/v1/ingestion/extract``  (multipart ``image`` + ``doc_type``)
    1. Validate the upload is ≤ 10 MB and in a supported image format; otherwise
       reject with a validation error and return no draft (Req 15.8).
    2. Dispatch to the matching ``ImageService.process_*`` method for the
       ``doc_type`` (Req 15.1).
    3. Shape the extraction result into a typed :class:`IngestionDraftResponse`.
    4. If nothing could be extracted (an error, or an empty result), report an
       extraction failure and return no draft (Req 15.9 → 422).

- ``POST /api/v1/ingestion/confirm``  (``{ doc_type, draft }``)
    Routes the (possibly user-edited) draft to the matching domain create:
      receipt  → expense create (``PurchaseExpense``)
      recipe   → ``RecipeService.create_recipe`` + ``add_component``
      order    → ``OrderService.create_order``
      catalog  → ``ProductService.create_product`` (per product)
      payment  → ``PaymentService.record_payment``
    On any domain failure the router persists **nothing** from that draft
    (compensating cleanup for the multi-step recipe/catalog cases), and reports
    the failure (Req 15.4, 15.10). "Discard" is purely client-side — no
    persistence happens until ``confirm`` (Req 15.5).

_Requirements: 15.1, 15.4, 15.5, 15.6, 15.8, 15.9, 15.10_
"""

from __future__ import annotations

import io
import logging
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, List, Optional, Tuple

from fastapi import APIRouter, Depends, File, Form, UploadFile
from PIL import Image
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api import errors
from app.api.deps import AuthedUser, get_current_user, get_tenant_db_for_user
from app.api.schemas import (
    serialize_expense,
    serialize_inventory_item,
    serialize_order,
    serialize_payment,
    serialize_recipe,
)
from app.models import PurchaseExpense
from app.services.image_service import ImageService
from app.services.inventory_service import InventoryService
from app.services.order_service import OrderCreate, OrderItemCreate, OrderService
from app.services.payment_service import PaymentCreate, PaymentService
from app.services.product_service import ProductService, VariantInput
from app.services.recipe_service import RecipeService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/ingestion", tags=["ingestion"])


# ── Constants ─────────────────────────────────────────────────────────────────

# Req 15.8: reject uploads larger than 10 MB.
MAX_IMAGE_BYTES = 10 * 1024 * 1024

# Req 15.8: the image formats we accept (as reported by Pillow). Anything else —
# or an unreadable/non-image payload — is rejected as a validation error.
SUPPORTED_IMAGE_FORMATS = frozenset({"JPEG", "PNG", "WEBP", "GIF", "BMP", "TIFF"})

# The five ingestion document types supported by the App (Req 15.6).
DOC_RECEIPT = "receipt"
DOC_RECIPE = "recipe"
DOC_ORDER = "order"
DOC_CATALOG = "catalog"
DOC_PAYMENT = "payment"
DOC_INVENTORY = "inventory"
SUPPORTED_DOC_TYPES = frozenset(
    {DOC_RECEIPT, DOC_RECIPE, DOC_ORDER, DOC_CATALOG, DOC_PAYMENT, DOC_INVENTORY}
)

# doc_type → the existing ImageService method used for extraction. A payment
# receipt is a receipt image, so it reuses the receipt extractor (which already
# pulls amount / method / date / payer) — ImageService is reused unchanged. A
# purchase receipt scanned *into inventory* is likewise a receipt image, so
# ``inventory`` reuses the same extractor (its line items become stock rows).
_EXTRACTORS: Dict[str, str] = {
    DOC_RECEIPT: "process_receipt_image",
    DOC_PAYMENT: "process_receipt_image",
    DOC_INVENTORY: "process_receipt_image",
    DOC_RECIPE: "process_recipe_image",
    DOC_ORDER: "process_order_image",
    DOC_CATALOG: "process_catalog_image",
}


# ── Typed Ingestion_Draft models (extract response) ───────────────────────────

class ReceiptItemDraft(BaseModel):
    name: Optional[str] = None
    quantity: Optional[float] = None
    unit: Optional[str] = None
    price: Optional[float] = None


class ReceiptDraft(BaseModel):
    """Shape for receipt (→ expense) and payment (→ payment) extraction."""

    amount: Optional[float] = None
    method: Optional[str] = None
    date: Optional[str] = None
    customer_name: Optional[str] = None
    items: List[ReceiptItemDraft] = Field(default_factory=list)


class InventoryItemDraft(BaseModel):
    """A single stock row derived from a scanned purchase-receipt line item.

    The receipt item ``price`` is mapped to ``cost`` (cost per unit) and
    ``category`` is left blank for the owner to fill in on the confirm form.
    """

    name: Optional[str] = None
    quantity: Optional[float] = None
    unit: Optional[str] = None
    cost: Optional[float] = None
    category: Optional[str] = None


class InventoryDraft(BaseModel):
    """Shape for inventory extraction — a list of stock rows to review/edit."""

    items: List[InventoryItemDraft] = Field(default_factory=list)


class RecipeComponentDraft(BaseModel):
    item_name: Optional[str] = None
    quantity: Optional[float] = None
    unit: Optional[str] = None


class RecipeDraft(BaseModel):
    name: Optional[str] = None
    yield_per_batch: Optional[int] = None
    ingredients: List[RecipeComponentDraft] = Field(default_factory=list)
    packaging: List[RecipeComponentDraft] = Field(default_factory=list)


class OrderItemDraft(BaseModel):
    recipe_name: Optional[str] = None
    quantity: Optional[float] = None
    selling_price: Optional[float] = None


class OrderDraft(BaseModel):
    customer_name: Optional[str] = None
    customer_phone: Optional[str] = None
    delivery_date: Optional[str] = None
    items: List[OrderItemDraft] = Field(default_factory=list)


class CatalogVariantDraft(BaseModel):
    size_label: Optional[str] = None
    price: Optional[float] = None


class CatalogProductDraft(BaseModel):
    name: Optional[str] = None
    variants: List[CatalogVariantDraft] = Field(default_factory=list)


class CatalogCategoryDraft(BaseModel):
    name: Optional[str] = None
    products: List[CatalogProductDraft] = Field(default_factory=list)


class CatalogDraft(BaseModel):
    categories: List[CatalogCategoryDraft] = Field(default_factory=list)


class IngestionDraftResponse(BaseModel):
    """
    The typed ``Ingestion_Draft`` returned by ``/extract``.

    ``draft`` carries the doc-type-specific structured fields (already validated
    against the typed model above and serialized to a dict for the editable UI
    form). ``confidence`` and ``raw_text`` echo the extractor's self-assessment.
    """

    doc_type: str
    confidence: float = 0.0
    raw_text: Optional[str] = None
    draft: Dict[str, Any]


# ── Confirm request ────────────────────────────────────────────────────────────

class IngestionConfirmRequest(BaseModel):
    """Body for ``/confirm`` — the (possibly user-edited) draft to persist."""

    doc_type: str
    draft: Dict[str, Any] = Field(default_factory=dict)


# ── Image service dependency ───────────────────────────────────────────────────

def get_image_service() -> ImageService:
    """
    Build the vision extraction service.

    Constructed per request (the underlying LLM client is cheap to create and
    this keeps import-time side effects out of the module). Tests exercise the
    route callable directly with a stub image service, so this is only invoked
    on real HTTP requests.
    """
    from app.services.llm_service import LLMService

    return ImageService(LLMService())


# ── Validation helpers ──────────────────────────────────────────────────────────

def _validate_image(image_bytes: bytes) -> None:
    """
    Enforce Req 15.8: reject an empty upload, one larger than 10 MB, or one that
    is not a readable, supported image format. Raises :class:`errors.ValidationError`
    (400) so no draft is produced.
    """
    if not image_bytes:
        raise errors.ValidationError(detail="Image file is empty", field="file")

    if len(image_bytes) > MAX_IMAGE_BYTES:
        limit_mb = MAX_IMAGE_BYTES // (1024 * 1024)
        raise errors.ValidationError(
            detail=f"Image exceeds the {limit_mb} MB limit", field="file"
        )

    try:
        with Image.open(io.BytesIO(image_bytes)) as img:
            fmt = (img.format or "").upper()
    except Exception as exc:  # noqa: BLE001 - any decode failure is a bad format
        raise errors.ValidationError(
            detail="Unsupported or unreadable image format", field="file"
        ) from exc

    if fmt not in SUPPORTED_IMAGE_FORMATS:
        raise errors.ValidationError(
            detail=f"Unsupported image format: {fmt or 'unknown'}", field="file"
        )


def _parse_date(value: Any, *, field: str, required: bool) -> Optional[date]:
    """
    Parse an ISO date string. When ``required`` an absent/invalid value raises a
    validation error; otherwise absent/invalid yields ``None`` (the caller may
    substitute a default).
    """
    if value is None or value == "":
        if required:
            raise errors.ValidationError(detail=f"{field} is required", field=field)
        return None
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value))
    except (ValueError, TypeError) as exc:
        if required:
            raise errors.ValidationError(
                detail=f"Invalid {field}: {value}", field=field
            ) from exc
        return None


def _to_decimal(value: Any, *, field: str) -> Decimal:
    """Coerce a value to Decimal or raise a validation error."""
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise errors.ValidationError(detail=f"Invalid {field}: {value}", field=field) from exc


# ── Extraction result → typed draft ─────────────────────────────────────────────

def _shape_receipt(result: Dict[str, Any]) -> Tuple[ReceiptDraft, bool]:
    draft = ReceiptDraft(
        amount=result.get("amount"),
        method=result.get("method"),
        date=result.get("date"),
        customer_name=result.get("customer_name"),
        items=[
            ReceiptItemDraft(
                name=it.get("name"),
                quantity=it.get("quantity"),
                unit=it.get("unit"),
                price=it.get("price"),
            )
            for it in (result.get("items") or [])
            if isinstance(it, dict)
        ],
    )
    has_content = (
        draft.amount is not None or bool(draft.items) or bool(draft.customer_name)
    )
    return draft, has_content


def _shape_inventory(result: Dict[str, Any]) -> Tuple[InventoryDraft, bool]:
    """Shape a receipt extraction into an inventory draft.

    Each receipt line item becomes a stock row: its ``price`` maps to ``cost``
    (cost per unit) and ``category`` is left blank for the owner to fill in on
    the confirm form. "Has content" means at least one item was extracted.
    """
    draft = InventoryDraft(
        items=[
            InventoryItemDraft(
                name=it.get("name"),
                quantity=it.get("quantity"),
                unit=it.get("unit"),
                cost=it.get("price"),
                category=None,
            )
            for it in (result.get("items") or [])
            if isinstance(it, dict)
        ]
    )
    has_content = bool(draft.items)
    return draft, has_content


def _shape_recipe(result: Dict[str, Any]) -> Tuple[RecipeDraft, bool]:
    def _components(key: str) -> List[RecipeComponentDraft]:
        return [
            RecipeComponentDraft(
                item_name=c.get("item_name"),
                quantity=c.get("quantity"),
                unit=c.get("unit"),
            )
            for c in (result.get(key) or [])
            if isinstance(c, dict)
        ]

    draft = RecipeDraft(
        name=result.get("name"),
        yield_per_batch=result.get("yield_per_batch"),
        ingredients=_components("ingredients"),
        packaging=_components("packaging"),
    )
    has_content = bool(draft.name) or bool(draft.ingredients) or bool(draft.packaging)
    return draft, has_content


def _shape_order(result: Dict[str, Any]) -> Tuple[OrderDraft, bool]:
    draft = OrderDraft(
        customer_name=result.get("customer_name"),
        customer_phone=result.get("customer_phone"),
        delivery_date=result.get("delivery_date"),
        items=[
            OrderItemDraft(
                recipe_name=it.get("recipe_name"),
                quantity=it.get("quantity"),
                selling_price=it.get("selling_price"),
            )
            for it in (result.get("items") or [])
            if isinstance(it, dict)
        ],
    )
    has_content = (
        bool(draft.items) or bool(draft.customer_name) or bool(draft.customer_phone)
    )
    return draft, has_content


def _shape_catalog(result: Dict[str, Any]) -> Tuple[CatalogDraft, bool]:
    categories: List[CatalogCategoryDraft] = []
    for cat in result.get("categories") or []:
        if not isinstance(cat, dict):
            continue
        products = [
            CatalogProductDraft(
                name=p.get("name"),
                variants=[
                    CatalogVariantDraft(
                        size_label=v.get("size_label"), price=v.get("price")
                    )
                    for v in (p.get("variants") or [])
                    if isinstance(v, dict)
                ],
            )
            for p in (cat.get("products") or [])
            if isinstance(p, dict)
        ]
        categories.append(CatalogCategoryDraft(name=cat.get("name"), products=products))

    draft = CatalogDraft(categories=categories)
    has_content = any(cat.products for cat in draft.categories)
    return draft, has_content


def _shape_draft(doc_type: str, result: Dict[str, Any]) -> Tuple[BaseModel, bool]:
    """Shape an extraction ``result`` into the typed draft for ``doc_type``."""
    if doc_type in (DOC_RECEIPT, DOC_PAYMENT):
        return _shape_receipt(result)
    if doc_type == DOC_INVENTORY:
        return _shape_inventory(result)
    if doc_type == DOC_RECIPE:
        return _shape_recipe(result)
    if doc_type == DOC_ORDER:
        return _shape_order(result)
    if doc_type == DOC_CATALOG:
        return _shape_catalog(result)
    # Unreachable: doc_type is validated before this is called.
    raise errors.ValidationError(detail=f"Unsupported doc_type '{doc_type}'", field="doc_type")


# ── Confirm handlers (draft → domain create) ─────────────────────────────────────

def _confirm_receipt(db: Session, user: AuthedUser, draft: Dict[str, Any]) -> Dict[str, Any]:
    """receipt → expense create (``PurchaseExpense``). Persists nothing on failure."""
    if draft.get("amount") is None:
        raise errors.ValidationError(detail="Expense amount is required", field="amount")
    amount = _to_decimal(draft.get("amount"), field="amount")
    if amount <= 0:
        raise errors.ValidationError(detail="Amount must be positive", field="amount")

    category = str(draft.get("category") or "other").strip().lower()
    if category not in PurchaseExpense.CATEGORIES:
        raise errors.ValidationError(
            detail=(
                f"Invalid category '{category}'. Valid: "
                f"{', '.join(PurchaseExpense.CATEGORIES)}"
            ),
            field="category",
        )

    expense_date = (
        _parse_date(draft.get("expense_date") or draft.get("date"), field="expense_date", required=False)
        or date.today()
    )
    is_capital = str(draft.get("is_capital", False)).strip().lower() in ("true", "1", "yes")

    expense = PurchaseExpense(
        tenant_id=user.tenant_id,
        amount=amount,
        vendor_name=draft.get("vendor_name") or draft.get("customer_name"),
        expense_date=expense_date,
        category=category,
        is_capital="true" if is_capital else "false",
        description=draft.get("description"),
        notes=draft.get("notes"),
    )
    try:
        db.add(expense)
        db.commit()
        db.refresh(expense)
    except Exception:
        db.rollback()
        raise
    return serialize_expense(expense, user)


def _confirm_payment(db: Session, user: AuthedUser, draft: Dict[str, Any]) -> Dict[str, Any]:
    """payment → ``PaymentService.record_payment``. Service validates + persists atomically."""
    order_identifier = (
        draft.get("order_identifier")
        or draft.get("order_id")
        or draft.get("customer_name")
        or draft.get("customer_phone")
    )
    if not order_identifier:
        raise errors.ValidationError(
            detail="An order id or customer identifier is required", field="order_identifier"
        )

    payment = PaymentCreate(
        order_identifier=str(order_identifier),
        amount=draft.get("amount"),
        method=str(draft.get("method") or ""),
    )
    with errors.map_service_errors():
        recorded = PaymentService(db).record_payment(user.tenant_id, payment)
    return serialize_payment(recorded, user)


def _confirm_recipe(db: Session, user: AuthedUser, draft: Dict[str, Any]) -> Dict[str, Any]:
    """
    recipe → ``create_recipe`` + ``add_component`` per component.

    Multi-step, so to honor Req 15.10 ("persist nothing on failure") the created
    recipe is deleted (compensated) if any component fails to attach.
    """
    service = RecipeService(db)
    with errors.map_service_errors():
        recipe = service.create_recipe(
            tenant_id=user.tenant_id,
            name=draft.get("name"),
            yield_per_batch=draft.get("yield_per_batch"),
        )
        try:
            for comp in draft.get("ingredients") or []:
                service.add_component(
                    tenant_id=user.tenant_id,
                    recipe_name=recipe.name,
                    item_name=comp.get("item_name"),
                    quantity=comp.get("quantity"),
                    component_type="ingredient",
                )
            for comp in draft.get("packaging") or []:
                service.add_component(
                    tenant_id=user.tenant_id,
                    recipe_name=recipe.name,
                    item_name=comp.get("item_name"),
                    quantity=comp.get("quantity"),
                    component_type="packaging",
                )
        except (ValueError, errors.APIError):
            # Compensate: remove the just-created recipe so nothing is persisted.
            try:
                service.delete_recipe(user.tenant_id, recipe.name)
            except Exception:  # noqa: BLE001
                db.rollback()
            raise
    return serialize_recipe(recipe, user)


def _confirm_order(db: Session, user: AuthedUser, draft: Dict[str, Any]) -> Dict[str, Any]:
    """order → ``OrderService.create_order``. Service validates + persists atomically."""
    customer_identifier = (
        draft.get("customer_identifier")
        or draft.get("customer_phone")
        or draft.get("customer_name")
    )
    if not customer_identifier:
        raise errors.ValidationError(detail="A customer is required", field="customer")

    delivery_date = _parse_date(draft.get("delivery_date"), field="delivery_date", required=True)

    items = [
        OrderItemCreate(
            recipe_name=it.get("recipe_name"),
            quantity=it.get("quantity"),
            selling_price=it.get("selling_price"),
        )
        for it in (draft.get("items") or [])
    ]

    order_create = OrderCreate(
        customer_identifier=str(customer_identifier),
        delivery_date=delivery_date,
        items=items,
        delivery_address=draft.get("delivery_address"),
    )
    with errors.map_service_errors():
        order = OrderService(db).create_order(user.tenant_id, order_create)
    return serialize_order(order, user)


def _confirm_catalog(db: Session, user: AuthedUser, draft: Dict[str, Any]) -> Dict[str, Any]:
    """
    catalog → ``ProductService.create_product`` per product.

    All products are validated up front (name + at least one priced variant),
    then created. If a create fails mid-way, products created in this confirm
    are deleted so nothing is persisted (Req 15.10).
    """
    service = ProductService(db)

    # Flatten categories → products, supporting both the nested catalog shape
    # and a flat ``products`` list.
    flattened: List[Tuple[Optional[str], Dict[str, Any]]] = []
    for cat in draft.get("categories") or []:
        cat_name = cat.get("name")
        for product in cat.get("products") or []:
            flattened.append((cat_name, product))
    if not flattened:
        for product in draft.get("products") or []:
            flattened.append((draft.get("category"), product))

    if not flattened:
        raise errors.ValidationError(detail="No products to create", field="products")

    # Pre-validate so we create nothing if any product is malformed.
    prepared: List[Tuple[Optional[str], str, List[VariantInput]]] = []
    for cat_name, product in flattened:
        name = (product.get("name") or "").strip()
        if not name:
            raise errors.ValidationError(detail="Product name is required", field="name")
        variants = [
            VariantInput(
                size_label=str(v.get("size_label") or "standard"),
                price=_to_decimal(v.get("price"), field="price"),
            )
            for v in (product.get("variants") or [])
            if v.get("price") is not None
        ]
        if not variants:
            raise errors.ValidationError(
                detail=f"Product '{name}' needs at least one variant with a price",
                field="variants",
            )
        prepared.append((cat_name, name, variants))

    created: List[Any] = []
    with errors.map_service_errors():
        try:
            for cat_name, name, variants in prepared:
                product = service.create_product(
                    tenant_id=user.tenant_id,
                    name=name,
                    variants=variants,
                    category=cat_name,
                )
                created.append(product)
        except (ValueError, errors.APIError):
            for product in created:
                try:
                    service.delete_product(user.tenant_id, product.name)
                except Exception:  # noqa: BLE001
                    pass
            raise

    return {
        "products": [
            {"product_id": p.product_id, "name": p.name, "category": p.category}
            for p in created
        ]
    }


def _confirm_inventory(db: Session, user: AuthedUser, draft: Dict[str, Any]) -> Dict[str, Any]:
    """
    inventory → ``InventoryService.create_item`` per stock row (Req 15.6).

    Each draft item is created as an inventory item. ``category`` defaults to
    ``"ingredient"`` when the owner left it blank, and the receipt-derived
    ``cost`` becomes ``cost_per_unit``.

    Multi-step (one commit per item, no batch transaction in the service), so to
    honor Req 15.10 ("persist nothing on domain failure") all rows are
    pre-validated up front; if a create still fails mid-batch, the rows already
    created in this confirm are deleted so nothing is persisted.
    """
    raw_items = draft.get("items") or []
    if not raw_items:
        raise errors.ValidationError(detail="No inventory items to create", field="items")

    service = InventoryService(db)

    # Pre-validate + coerce every row first so a malformed row creates nothing.
    prepared: List[Dict[str, Any]] = []
    for item in raw_items:
        if not isinstance(item, dict):
            raise errors.ValidationError(detail="Invalid inventory item", field="items")
        name = (item.get("name") or "").strip()
        if not name:
            raise errors.ValidationError(detail="Item name is required", field="name")
        if item.get("quantity") is None:
            raise errors.ValidationError(detail=f"Quantity is required for '{name}'", field="quantity")
        if item.get("cost") is None:
            raise errors.ValidationError(detail=f"Cost is required for '{name}'", field="cost")
        unit = (item.get("unit") or "").strip()
        if not unit:
            raise errors.ValidationError(detail=f"Unit is required for '{name}'", field="unit")

        category = (item.get("category") or "").strip().lower() or "ingredient"
        quantity = _to_decimal(item.get("quantity"), field="quantity")
        cost = _to_decimal(item.get("cost"), field="cost")
        prepared.append(
            {
                "name": name,
                "category": category,
                "quantity": quantity,
                "unit": unit,
                "cost_per_unit": cost,
            }
        )

    created: List[Any] = []
    with errors.map_service_errors():
        try:
            for row in prepared:
                created.append(
                    service.create_item(
                        tenant_id=user.tenant_id,
                        name=row["name"],
                        category=row["category"],
                        quantity=row["quantity"],
                        unit=row["unit"],
                        cost_per_unit=row["cost_per_unit"],
                    )
                )
        except (ValueError, errors.APIError):
            # Compensate: delete the rows created in this confirm so nothing is
            # persisted (Req 15.10). The service commits per item, so we cannot
            # rely on a single rollback.
            for made in created:
                try:
                    persisted = service.get_item_by_id(user.tenant_id, made.item_id)
                    if persisted is not None:
                        db.delete(persisted)
                        db.commit()
                except Exception:  # noqa: BLE001
                    db.rollback()
            raise

    return {"items": [serialize_inventory_item(i, user) for i in created]}


_CONFIRM_HANDLERS = {
    DOC_RECEIPT: _confirm_receipt,
    DOC_PAYMENT: _confirm_payment,
    DOC_INVENTORY: _confirm_inventory,
    DOC_RECIPE: _confirm_recipe,
    DOC_ORDER: _confirm_order,
    DOC_CATALOG: _confirm_catalog,
}


# ── Routes ────────────────────────────────────────────────────────────────────

@router.post("/extract")
async def extract(
    doc_type: str = Form(...),
    image: UploadFile = File(...),
    user: AuthedUser = Depends(get_current_user),
    image_service: ImageService = Depends(get_image_service),
) -> IngestionDraftResponse:
    """
    Extract a typed ``Ingestion_Draft`` from an uploaded image (Req 15.1).

    Validates the upload (≤ 10 MB, supported format — Req 15.8) before calling
    the matching ``ImageService.process_*`` method, then shapes the result into a
    typed draft. Returns 422 (extraction failure) when the extractor errors or
    produces no structured data (Req 15.9).
    """
    dt = doc_type.strip().lower()
    if dt not in SUPPORTED_DOC_TYPES:
        raise errors.ValidationError(
            detail=(
                f"Unsupported doc_type '{doc_type}'. Supported: "
                f"{', '.join(sorted(SUPPORTED_DOC_TYPES))}"
            ),
            field="doc_type",
        )

    image_bytes = await image.read()
    _validate_image(image_bytes)

    method_name = _EXTRACTORS[dt]
    try:
        result = await getattr(image_service, method_name)(image_bytes)
    except Exception as exc:  # noqa: BLE001 - any extractor failure → 422
        logger.warning("Image extraction failed for doc_type=%s: %s", dt, exc)
        raise errors.ExtractionFailedError() from exc

    # ImageService signals a failed extraction with an ``error`` key.
    if not isinstance(result, dict) or result.get("error"):
        raise errors.ExtractionFailedError()

    draft_model, has_content = _shape_draft(dt, result)
    if not has_content:
        # Nothing structured could be extracted (Req 15.9).
        raise errors.ExtractionFailedError()

    return IngestionDraftResponse(
        doc_type=dt,
        confidence=float(result.get("confidence") or 0.0),
        raw_text=result.get("raw_text"),
        draft=draft_model.model_dump(),
    )


@router.post("/confirm")
def confirm(
    body: IngestionConfirmRequest,
    user: AuthedUser = Depends(get_current_user),
    db: Session = Depends(get_tenant_db_for_user),
) -> Dict[str, Any]:
    """
    Persist a confirmed (user-edited) draft via its matching domain create
    (Req 15.4, 15.6). On any domain failure nothing is persisted (Req 15.10).
    Discard is handled entirely client-side — this endpoint is only reached on
    confirm (Req 15.5).
    """
    dt = body.doc_type.strip().lower()
    handler = _CONFIRM_HANDLERS.get(dt)
    if handler is None:
        raise errors.ValidationError(
            detail=(
                f"Unsupported doc_type '{body.doc_type}'. Supported: "
                f"{', '.join(sorted(SUPPORTED_DOC_TYPES))}"
            ),
            field="doc_type",
        )
    return handler(db, user, body.draft or {})
