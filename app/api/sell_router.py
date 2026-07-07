"""
Sell / Point-of-Sale router for the app-first REST API (`/api/v1/sell`).

This is a thin HTTP adapter over the **existing, unchanged** ``BoothService``
(Req 8.6). The Sell surface reuses ``BoothService`` for totals and record
creation exactly as the booth router does; this router layers three app-first
concerns on top:

1. **Authenticated, tenant-scoped access (Owner + Staff).** Every endpoint
   depends on :func:`app.api.deps.get_current_user` and
   :func:`app.api.deps.get_tenant_db_for_user`, so the tenant is derived from
   the device session token (never a path parameter) and an unauthenticated
   request is rejected with 401 before any checkout runs (Req 7.5).

2. **Sales attribution (Req 7).** On checkout the acting user's id is stamped
   onto ``Order.created_by_user_id`` *inside the same DB transaction* that
   ``BoothService.checkout`` uses to persist the sale. A ``before_flush``
   listener registered on the session for the duration of the checkout call
   sets the attribution on the new ``Order`` as it is flushed, so the
   attribution is written atomically with the order INSERT — never in a
   separate follow-up transaction (Req 7.1, 7.4).

3. **Idempotency (Req 8.3, 18.5).** A client-supplied ``Idempotency-Key``
   header maps to a :class:`~app.models.SellIdempotency` row in the tenant DB.
   On a duplicate key the already-created order is returned instead of creating
   a second one, so offline replays / retries persist at most once.

Endpoints:

- ``GET  /api/v1/sell/session``            → active session + items for sale (Req 8.1)
- ``POST /api/v1/sell/products``           → Owner "Add item": create product + sync (Req 8.1)
- ``POST /api/v1/sell/checkout``           → create order + payment, attributed (Req 7, 8.3–8.8)
- ``GET  /api/v1/sell/receipt/{order_id}`` → printable receipt data (Req 8.5)
- ``GET  /api/v1/sell/invoice/{order_id}`` → downloadable PDF invoice (Req 8.5)

_Requirements: 7.1, 7.4, 7.5, 8.1, 8.3, 8.4, 8.5, 8.6, 8.7, 8.8, 18.5_
"""

from __future__ import annotations

import base64
import logging
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, List, Literal, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, Header
from pydantic import BaseModel, Field
from sqlalchemy import event
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api import errors
from app.api.deps import (
    AuthedUser,
    get_current_user,
    get_tenant_db_for_user,
    require_owner,
)
from app.api.schemas import apply_role_visibility
from app.booth.booth_service import BoothItemInput, BoothService
from app.models import BoothSession, Order, OrderItem, Payment, SellIdempotency
from app.services.product_service import ProductService, VariantInput

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/sell", tags=["sell"])


# ── Request schemas ────────────────────────────────────────────────────────────

class SellCheckoutItem(BaseModel):
    """One line in a Sell checkout cart. Mirrors ``BoothItemInput``."""

    variant_id: str
    quantity: int = Field(ge=1)
    extra_charge: Decimal = Field(default=Decimal("0"), ge=0)
    extra_note: Optional[str] = None


class SellCheckoutRequest(BaseModel):
    """Body for ``POST /api/v1/sell/checkout``.

    ``session_id`` is optional — when omitted the tenant's active session is
    used, matching the single-active-session model of ``BoothService``.
    """

    items: List[SellCheckoutItem] = Field(default_factory=list)
    payment_method: Literal["cash", "upi"]
    session_id: Optional[str] = None
    customer_name: Optional[str] = None
    gst_rate: Decimal = Field(default=Decimal("0"), ge=0)


class SellProductRequest(BaseModel):
    """Body for ``POST /api/v1/sell/products`` — the Owner's "Add item" form.

    Creates a single-variant product and syncs it into the always-on Sell
    session so it appears in the tap-to-sell grid immediately. ``size_label``
    defaults to ``"standard"`` for the common single-size case.
    """

    name: str
    category: Optional[str] = None
    size_label: str = "standard"
    price: Decimal = Field(gt=0)


# ── Helpers ────────────────────────────────────────────────────────────────────


# Sentinel session name for the app-first "always-on" Sell session (Req 8.1).
_SELL_SESSION_NAME = "Sell"


def _ensure_session_synced(svc: BoothService, db: Session, tenant_id: UUID) -> BoothSession:
    """Guarantee an active Sell session that mirrors the current catalog.

    The app-first Sell surface uses a single "always-on" session with unlimited
    stock instead of the booth's manual event sessions. This helper:

    1. Starts an ``"Sell"`` session (``mode="regular"``) when none is active, so
       the grid is never empty merely because no session was opened.
    2. Adds every catalog variant that is not already a session item, using the
       variant's own price as the booth price and ``stock_qty=None`` (unlimited).
       Variants already present are skipped (pre-checked by id, and the
       "already in this session" ``ValueError`` is caught defensively).

    Returns the active session. When the tenant has no products the session is
    valid but simply carries zero items.
    """
    session = svc.get_active_session()
    if session is None:
        session = svc.start_session(_SELL_SESSION_NAME, mode="regular")

    # Existing variants in the session — skip these so we never re-add.
    existing_ids = {item.variant_id for item in svc.list_items(session.session_id)}

    for product in ProductService(db).list_products(tenant_id):
        for variant in product.variants:
            if variant.variant_id in existing_ids:
                continue
            try:
                svc.add_item(
                    session.session_id,
                    variant.variant_id,
                    booth_price=variant.price,
                    stock_qty=None,  # unlimited stock for the always-on Sell session
                )
                existing_ids.add(variant.variant_id)
            except ValueError:
                # Defensive: a concurrent add or an already-present variant.
                existing_ids.add(variant.variant_id)

    return session

def _parse_uuid(value: str, field: str) -> UUID:
    """Parse a UUID string, raising a 400 validation error on failure."""
    try:
        return UUID(str(value))
    except (ValueError, AttributeError, TypeError):
        raise errors.ValidationError(detail=f"Invalid {field}", field=field)


def _build_sale_response(
    db: Session,
    tenant_id: UUID,
    order_id: UUID,
    user: AuthedUser,
    *,
    idempotent_replay: bool = False,
) -> Dict[str, Any]:
    """Build the checkout/sale response dict from the persisted order.

    Reads the order, its line items, and payment straight from the tenant DB so
    a fresh checkout and an idempotent replay return an identical shape. Applies
    role visibility (defense-in-depth) before returning.
    """
    order = (
        db.query(Order)
        .filter(Order.order_id == order_id, Order.tenant_id == tenant_id)
        .first()
    )
    if order is None:
        raise errors.NotFoundError()

    order_items = db.query(OrderItem).filter(OrderItem.order_id == order_id).all()
    payment = db.query(Payment).filter(Payment.order_id == order_id).first()

    items = [
        {
            "recipe_name": oi.recipe_name,
            "quantity": oi.quantity,
            "unit_price": float(oi.selling_price),
            "customization_charge": float(oi.customization_charge or 0),
            "customization_note": oi.customization_note or "",
            "line_total": float(
                oi.quantity * oi.selling_price + (oi.customization_charge or Decimal("0"))
            ),
        }
        for oi in order_items
    ]
    subtotal = sum(i["line_total"] for i in items)

    data: Dict[str, Any] = {
        "order_id": str(order.order_id),
        "created_by_user_id": (
            str(order.created_by_user_id) if order.created_by_user_id else None
        ),
        "status": order.status,
        "subtotal": subtotal,
        "total_amount": float(payment.amount) if payment else subtotal,
        "payment_id": str(payment.payment_id) if payment else None,
        "payment_method": payment.method if payment else None,
        "payment_status": payment.status if payment else None,
        "items": items,
        "idempotent_replay": idempotent_replay,
    }
    return apply_role_visibility(data, user)


def _tenant_invoice_context(tenant_id: UUID) -> tuple[str, str]:
    """Resolve (business_name, currency) for a tenant from the registry DB."""
    from app.database import get_registry_db
    from app.models import Tenant
    from app.services.tenant_service import TenantService

    reg_db = next(get_registry_db())
    try:
        tenant = reg_db.query(Tenant).filter(Tenant.tenant_id == tenant_id).first()
        business_name = (tenant.business_name if tenant else None) or "My Business"
        currency = (
            TenantService.currency_for_country(tenant.country or "india")
            if tenant
            else "Rs."
        )
        return business_name, currency
    finally:
        reg_db.close()


# ── Routes ─────────────────────────────────────────────────────────────────────

@router.get("/session")
def get_sell_session(
    user: AuthedUser = Depends(get_current_user),
    db: Session = Depends(get_tenant_db_for_user),
) -> Dict[str, Any]:
    """Return the active Sell session and the products available for sale.

    Reuses ``BoothService.get_active_session``/``list_items`` unchanged. Each
    item carries its unit price (booth price) and remaining stock so the Sell
    surface can render the tap-to-sell grid for the current tenant (Req 8.1).
    Auto-ends an expired event session first, mirroring the booth behaviour.
    """
    svc = BoothService(db, user.tenant_id)
    svc.auto_end_if_expired()

    # App-first "always-on" model: ensure an active Sell session exists and
    # mirrors the current catalog (unlimited stock), so the grid always reflects
    # the tenant's products and is never empty when products exist (Req 8.1).
    session = _ensure_session_synced(svc, db, user.tenant_id)

    items = svc.list_items(session.session_id)
    data = {
        "session": {
            "session_id": str(session.session_id),
            "name": session.name,
            "mode": session.mode or "regular",
            "started_at": session.started_at.isoformat() if session.started_at else None,
            "ends_at": session.ends_at.isoformat() if session.ends_at else None,
            "items": [
                {
                    "variant_id": str(i.variant_id),
                    "product_name": i.product_name,
                    "variant_label": i.variant_label,
                    "unit_price": float(i.booth_price),
                    "stock_qty": i.stock_qty,
                    "sold_qty": i.sold_qty,
                    "remaining": i.remaining,
                }
                for i in items
            ],
        }
    }
    return apply_role_visibility(data, user)


@router.post("/products")
def add_sell_product(
    body: SellProductRequest,
    user: AuthedUser = Depends(require_owner),
    db: Session = Depends(get_tenant_db_for_user),
) -> Dict[str, Any]:
    """Create a product (Owner "Add item") and add it to the Sell session (Req 8.1).

    Creates a single-variant product via ``ProductService.create_product`` and
    then re-syncs the always-on Sell session so the new variant is immediately
    available for sale with unlimited stock. Owner-only (``require_owner``);
    Staff receive 403. A duplicate product name surfaces as the service's
    mapped conflict error (409). Returns the created product's id, name,
    category, and variants.
    """
    with errors.map_service_errors():
        product = ProductService(db).create_product(
            user.tenant_id,
            body.name,
            [VariantInput(size_label=body.size_label, price=Decimal(str(body.price)))],
            category=body.category,
        )

    # Sync the new variant(s) into the always-on session so the grid shows it.
    svc = BoothService(db, user.tenant_id)
    _ensure_session_synced(svc, db, user.tenant_id)

    return {
        "product_id": str(product.product_id),
        "name": product.name,
        "category": product.category,
        "variants": [
            {
                "variant_id": str(v.variant_id),
                "size_label": v.size_label,
                "price": float(v.price),
            }
            for v in product.variants
        ],
    }


@router.post("/checkout")
def checkout(
    body: SellCheckoutRequest,
    user: AuthedUser = Depends(get_current_user),
    db: Session = Depends(get_tenant_db_for_user),
    idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
) -> Dict[str, Any]:
    """Process a Sell checkout: create exactly one order + one payment (Req 8.3).

    Behaviour:

    - **Empty cart** → rejected before any record is created (Req 8.8).
    - **Idempotency** → if an ``Idempotency-Key`` header maps to an existing
      order for this tenant, the existing order is returned and no second sale
      is created (Req 8.3, 18.5).
    - **Attribution** → the acting user's id is stamped on ``Order`` inside the
      same transaction that persists the sale, via a ``before_flush`` listener
      on the session (Req 7.1, 7.4).
    - **Failure** → any ``ValueError`` from ``BoothService`` (bad stock, closed
      session, invalid input) is translated to an HTTP error and no order or
      payment is persisted (Req 8.7).

    The unauthenticated case (Req 7.5) is handled upstream: ``get_current_user``
    rejects with 401 before this function runs, so no sale is created without an
    attributed user.
    """
    # Req 8.8 — reject an empty-cart checkout up front; create nothing.
    if not body.items:
        raise errors.ValidationError(detail="Cart is empty.", field="items")

    key = idempotency_key.strip() if idempotency_key else None

    # Req 8.3 / 18.5 — idempotent replay: return the already-created order.
    if key:
        existing = (
            db.query(SellIdempotency)
            .filter(
                SellIdempotency.idempotency_key == key,
                SellIdempotency.tenant_id == user.tenant_id,
            )
            .first()
        )
        if existing is not None:
            return _build_sale_response(
                db, user.tenant_id, existing.order_id, user, idempotent_replay=True
            )

    svc = BoothService(db, user.tenant_id)

    # Resolve the target session (explicit id or the tenant's active session).
    if body.session_id:
        session_id = _parse_uuid(body.session_id, "session_id")
    else:
        active = svc.get_active_session()
        if active is None:
            raise errors.ValidationError(
                detail="No active sell session.", field="session_id"
            )
        session_id = active.session_id

    # Build the service cart from the request.
    cart: List[BoothItemInput] = []
    for item in body.items:
        variant_id = _parse_uuid(item.variant_id, "variant_id")
        try:
            extra_charge = Decimal(str(item.extra_charge or 0))
        except (InvalidOperation, ValueError):
            raise errors.ValidationError(detail="Invalid extra_charge", field="extra_charge")
        cart.append(
            BoothItemInput(
                variant_id=variant_id,
                quantity=item.quantity,
                extra_charge=extra_charge,
                extra_note=item.extra_note,
            )
        )

    # Req 7.1 / 7.4 — stamp attribution atomically with the sale. The listener
    # fires during the flush that BoothService.checkout performs, so
    # created_by_user_id is written in the same transaction as the order INSERT.
    def _stamp_attribution(session, flush_context, instances):  # noqa: ANN001
        for obj in session.new:
            if isinstance(obj, Order) and obj.created_by_user_id is None:
                obj.created_by_user_id = user.user_id

    event.listen(db, "before_flush", _stamp_attribution)
    try:
        with errors.map_service_errors():
            result = svc.checkout(
                session_id=session_id,
                cart=cart,
                payment_method=body.payment_method,
                customer_name=body.customer_name,
                gst_rate=Decimal(str(body.gst_rate or 0)),
            )
    finally:
        event.remove(db, "before_flush", _stamp_attribution)

    order_id = result.order_id

    # Persist the idempotency mapping so a later replay of the same key returns
    # this order instead of creating a new one (Req 8.3, 18.5).
    if key:
        db.add(
            SellIdempotency(
                idempotency_key=key,
                tenant_id=user.tenant_id,
                order_id=order_id,
            )
        )
        try:
            db.commit()
        except IntegrityError:
            # A concurrent request already claimed this key — roll back the
            # mapping insert and return the order the winning request created.
            db.rollback()
            existing = (
                db.query(SellIdempotency)
                .filter(
                    SellIdempotency.idempotency_key == key,
                    SellIdempotency.tenant_id == user.tenant_id,
                )
                .first()
            )
            if existing is not None:
                return _build_sale_response(
                    db, user.tenant_id, existing.order_id, user, idempotent_replay=True
                )

    return _build_sale_response(db, user.tenant_id, order_id, user)


@router.get("/receipt/{order_id}")
def get_receipt(
    order_id: str,
    user: AuthedUser = Depends(get_current_user),
    db: Session = Depends(get_tenant_db_for_user),
) -> Dict[str, Any]:
    """Return printable receipt data for a completed sale (Req 8.5).

    Emits structured JSON (business name, line items, total, payment method) the
    Sell surface renders as a printable receipt. Scoped to the acting tenant.
    """
    oid = _parse_uuid(order_id, "order_id")

    order = (
        db.query(Order)
        .filter(Order.order_id == oid, Order.tenant_id == user.tenant_id)
        .first()
    )
    if order is None:
        raise errors.NotFoundError()

    order_items = db.query(OrderItem).filter(OrderItem.order_id == oid).all()
    payment = db.query(Payment).filter(Payment.order_id == oid).first()

    business_name, currency = _tenant_invoice_context(user.tenant_id)

    items = [
        {
            "name": oi.recipe_name,
            "quantity": oi.quantity,
            "unit_price": float(oi.selling_price),
            "line_total": float(
                oi.quantity * oi.selling_price + (oi.customization_charge or Decimal("0"))
            ),
        }
        for oi in order_items
    ]
    subtotal = sum(i["line_total"] for i in items)

    data = {
        "order_id": str(order.order_id),
        "receipt_number": str(order.order_id)[:8].upper(),
        "business_name": business_name,
        "currency": currency,
        "date": order.created_at.isoformat() if order.created_at else None,
        "items": items,
        "subtotal": subtotal,
        "total_amount": float(payment.amount) if payment else subtotal,
        "payment_method": payment.method if payment else None,
        "payment_status": payment.status if payment else None,
    }
    return apply_role_visibility(data, user)


@router.get("/invoice/{order_id}")
def get_invoice(
    order_id: str,
    tax_rate: float = 0,
    user: AuthedUser = Depends(get_current_user),
    db: Session = Depends(get_tenant_db_for_user),
) -> Dict[str, Any]:
    """Generate a downloadable PDF invoice for a sale (Req 8.5).

    Reuses ``InvoiceService.build_invoice_data``/``generate`` unchanged and
    returns the PDF base64-encoded, mirroring the booth invoice endpoint. An
    unknown order raises 404 (translated from the service ``ValueError``).
    """
    oid = _parse_uuid(order_id, "order_id")

    business_name, currency = _tenant_invoice_context(user.tenant_id)

    from app.services.invoice_service import InvoiceService

    svc = InvoiceService()
    with errors.map_service_errors():
        data = svc.build_invoice_data(
            db,
            user.tenant_id,
            oid,
            business_name,
            currency,
            tax_rate=Decimal(str(tax_rate or 0)),
        )
        pdf_bytes = svc.generate(data)

    return {
        "filename": f"invoice_{data.invoice_number}.pdf",
        "content_type": "application/pdf",
        "data": base64.b64encode(pdf_bytes).decode(),
    }
