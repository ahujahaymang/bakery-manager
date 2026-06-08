"""
Booth API router — all /booth/{tenant_id}/* endpoints.

Thin HTTP layer: validate input → call BoothService → return JSON.
No business logic here.

Endpoints:
  GET  /booth/{tenant_id}                        → serve booth.html SPA
  GET  /booth/{tenant_id}/api/session/active     → active session or null
  GET  /booth/{tenant_id}/api/session/{id}/items → items in a session
  POST /booth/{tenant_id}/api/checkout           → create order + payment
  GET  /booth/{tenant_id}/api/checkout/status/{order_id} → payment status
  GET  /booth/{tenant_id}/api/receipt/{order_id} → printable receipt HTML
"""

import logging
from decimal import Decimal
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.booth.booth_service import BoothItemInput, BoothService
from app.database import get_db
from app.models import Order, OrderItem, Payment, Tenant

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/register", tags=["register"])

# Keep /booth as an alias for backward compatibility
booth_alias = APIRouter(prefix="/booth", tags=["booth-alias"])

_TEMPLATES_DIR = Path(__file__).parent / "templates"
templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))


# ── Dependency: open tenant DB ─────────────────────────────────────────────────

def _get_tenant_db(tenant_id: str):
    """
    Validate tenant_id and yield a DB session for that tenant.
    Raises 404 if the tenant doesn't exist.
    """
    from app.database import get_registry_db

    # Validate UUID format
    try:
        tid = UUID(tenant_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="Invalid tenant ID")

    # Confirm tenant exists in registry
    reg_db = next(get_registry_db())
    try:
        tenant = reg_db.query(Tenant).filter(Tenant.tenant_id == tid).first()
        if not tenant:
            raise HTTPException(status_code=404, detail="Tenant not found")
    finally:
        reg_db.close()

    # Open tenant's business DB
    db = next(get_db(tid))
    try:
        yield db, tid
    finally:
        db.close()


# ── Pydantic schemas ───────────────────────────────────────────────────────────

class CheckoutItemSchema(BaseModel):
    variant_id: str
    quantity: int = Field(ge=1)
    extra_charge: float = 0        # packaging / customization charge
    extra_note: Optional[str] = None  # note for the extra charge


class CheckoutRequest(BaseModel):
    session_id: str
    items: List[CheckoutItemSchema]
    payment_method: Literal["cash", "upi", "razorpay"]
    customer_name: Optional[str] = None
    gst_rate: float = 0            # GST % to apply to the total (e.g. 5 for 5%)


class CreateSessionRequest(BaseModel):
    name: str
    mode: str = "regular"          # "regular" | "event"
    duration_days: Optional[int] = None
    items: List[Dict[str, Any]]


# ── Static files ───────────────────────────────────────────────────────────────

_STATIC_DIR = Path(__file__).parent / "static"


# ── Routes ─────────────────────────────────────────────────────────────────────

@router.get("/{tenant_id}/api/info")
async def get_booth_info(tenant_id: str, ctx=Depends(_get_tenant_db)) -> Dict[str, Any]:
    """Return basic tenant info for the booth UI."""
    db, tid = ctx
    from app.database import get_registry_db
    reg_db = next(get_registry_db())
    try:
        tenant = reg_db.query(Tenant).filter(Tenant.tenant_id == tid).first()
        return {"business_name": tenant.business_name if tenant else "Booth"}
    finally:
        reg_db.close()


@router.get("/{tenant_id}/api/catalog")
async def get_catalog(tenant_id: str, ctx=Depends(_get_tenant_db)) -> Dict[str, Any]:
    """Return the full product catalog grouped by category for the setup screen."""
    db, tid = ctx
    from app.services.product_service import ProductService
    svc = ProductService(db)
    products = svc.list_products(tid)

    by_cat: Dict[str, list] = {}
    for p in products:
        cat = p.category or "Other"
        if cat not in by_cat:
            by_cat[cat] = []
        by_cat[cat].append({
            "product_id": str(p.product_id),
            "name": p.name,
            "variants": [
                {
                    "variant_id": str(v.variant_id),
                    "size_label": v.size_label,
                    "price": float(v.price),
                }
                for v in sorted(p.variants, key=lambda x: x.price)
            ],
        })

    return {
        "categories": [
            {"name": cat, "products": prods}
            for cat, prods in sorted(by_cat.items())
        ]
    }


@router.post("/{tenant_id}/api/session/create")
async def create_session(
    tenant_id: str,
    body: CreateSessionRequest,
    ctx=Depends(_get_tenant_db),
) -> Dict[str, Any]:
    """Create a new booth session from the setup screen."""
    db, tid = ctx
    svc = BoothService(db, tid)

    # End any existing active session first
    existing = svc.get_active_session()
    if existing:
        svc.end_session(existing.session_id)

    session = svc.start_session(
        body.name,
        mode=body.mode,
        duration_days=body.duration_days,
    )
    added = 0
    errors = []

    for item in body.items:
        try:
            if item.get("custom"):
                from app.services.product_service import ProductService, VariantInput
                from decimal import Decimal as D
                prod_svc = ProductService(db)
                try:
                    product = prod_svc.create_product(
                        tenant_id=tid,
                        name=item["name"],
                        variants=[VariantInput(
                            size_label=item.get("size_label", "standard"),
                            price=D(str(item["booth_price"])),
                        )],
                    )
                except ValueError:
                    # Product already exists — find it
                    product = prod_svc.get_product(tid, item["name"])
                if product and product.variants:
                    variant = product.variants[0]
                    svc.add_item(
                        session_id=session.session_id,
                        variant_id=variant.variant_id,
                        booth_price=Decimal(str(item["booth_price"])),
                        stock_qty=None,
                    )
                    added += 1
            else:
                vid = item.get("variant_id")
                price = item.get("booth_price")
                if not vid or price is None:
                    errors.append(f"Missing variant_id or booth_price: {item}")
                    continue
                svc.add_item(
                    session_id=session.session_id,
                    variant_id=UUID(str(vid)),
                    booth_price=Decimal(str(price)),
                    stock_qty=None,
                )
                added += 1
        except Exception as e:
            logger.error(f"Failed to add booth item {item}: {e}")
            errors.append(str(e))

    return {
        "session_id": str(session.session_id),
        "name": session.name,
        "items_added": added,
        "errors": errors,
    }


@router.get("/{tenant_id}/api/session/orders")
async def get_session_orders(
    tenant_id: str,
    ctx=Depends(_get_tenant_db),
) -> Dict[str, Any]:
    """Return all orders for the active session."""
    from app.models import Customer
    db, tid = ctx
    svc = BoothService(db, tid)
    session = svc.get_active_session()
    if not session:
        return {"orders": []}

    orders = (
        db.query(Order)
        .filter(
            Order.tenant_id == tid,
            Order.booth_session_id == session.session_id,
            Order.status != "cancelled",
        )
        .order_by(Order.created_at.desc())
        .all()
    )

    result = []
    for order in orders:
        # Get customer name
        customer = db.query(Customer).filter(Customer.customer_id == order.customer_id).first()
        customer_name = customer.name if customer else "Walk-in"

        order_items = db.query(OrderItem).filter(OrderItem.order_id == order.order_id).all()
        payment = db.query(Payment).filter(Payment.order_id == order.order_id).first()

        # Compute total from items
        total = sum(float(oi.quantity * oi.selling_price) + float(oi.customization_charge or 0) for oi in order_items)

        result.append({
            "order_id": str(order.order_id),
            "customer_name": customer_name,
            "created_at": order.created_at.isoformat() if order.created_at else "",
            "total_amount": total,
            "payment_method": payment.method if payment else "—",
            "items": [
                {
                    "recipe_name": oi.recipe_name,
                    "quantity": oi.quantity,
                    "unit_price": float(oi.selling_price),
                    "customization_charge": float(oi.customization_charge or 0),
                    "customization_note": oi.customization_note or "",
                    "line_total": float(oi.quantity * oi.selling_price) + float(oi.customization_charge or 0),
                }
                for oi in order_items
            ],
        })

    return {"orders": result, "session_name": session.name}


async def end_session(tenant_id: str, ctx=Depends(_get_tenant_db)) -> Dict[str, Any]:
    """End the active booth session."""
    db, tid = ctx
    svc = BoothService(db, tid)
    session = svc.get_active_session()
    if not session:
        raise HTTPException(status_code=404, detail="No active session")
    closed = svc.end_session(session.session_id)
    summary = svc.get_session_summary(closed.session_id)
    return {
        "name": summary.name,
        "total_revenue": float(summary.total_revenue),
        "total_orders": summary.total_orders,
        "items_sold": summary.items_sold,
    }


@router.get("/{tenant_id}/api/invoice/{order_id}")
async def generate_invoice(
    tenant_id: str,
    order_id: str,
    request: Request,
    tax_rate: float = 0,
    ctx=Depends(_get_tenant_db),
):
    """Generate a PDF invoice for a register order. Returns base64-encoded PDF."""
    db, tid = ctx
    try:
        oid = UUID(order_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid order_id")

    from app.services.invoice_service import InvoiceService
    from app.database import get_registry_db
    from decimal import Decimal as D

    reg_db = next(get_registry_db())
    try:
        tenant = reg_db.query(Tenant).filter(Tenant.tenant_id == tid).first()
        business_name = tenant.business_name if tenant else "My Business"
        from app.services.tenant_service import TenantService
        currency = TenantService.currency_for_country(tenant.country or "india") if tenant else "Rs."
    finally:
        reg_db.close()

    svc = InvoiceService()
    try:
        data = svc.build_invoice_data(
            db, tid, oid, business_name, currency,
            tax_rate=D(str(tax_rate)),
        )
        pdf_bytes = svc.generate(data)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))

    import base64
    return {
        "filename": f"invoice_{data.invoice_number}.pdf",
        "data": base64.b64encode(pdf_bytes).decode(),
    }


@router.post("/{tenant_id}/api/cancel/{order_id}")
async def cancel_order_endpoint(
    tenant_id: str,
    order_id: str,
    ctx=Depends(_get_tenant_db),
) -> Dict[str, str]:
    """Cancel a booth order (void the sale)."""
    db, tid = ctx
    try:
        oid = UUID(order_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid order_id")

    order = db.query(Order).filter(Order.order_id == oid, Order.tenant_id == tid).first()
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")
    if order.status == "cancelled":
        raise HTTPException(status_code=400, detail="Order already cancelled")

    order.status = "cancelled"
    db.commit()
    return {"status": "cancelled"}

@router.get("/{tenant_id}", response_class=HTMLResponse)
async def serve_register(tenant_id: str, request: Request):
    """Serve the Register Mode SPA."""
    try:
        UUID(tenant_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="Invalid tenant ID")
    return templates.TemplateResponse(
        request=request, name="booth.html", context={"tenant_id": tenant_id},
    )


@booth_alias.get("/{tenant_id}", response_class=HTMLResponse)
async def serve_booth_alias(tenant_id: str, request: Request):
    """Backward-compat alias — /booth/{id} → same page as /register/{id}."""
    try:
        UUID(tenant_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="Invalid tenant ID")
    return templates.TemplateResponse(
        request=request, name="booth.html", context={"tenant_id": tenant_id},
    )


@router.get("/{tenant_id}/api/session/active")
async def get_active_session(
    tenant_id: str,
    ctx=Depends(_get_tenant_db),
) -> Dict[str, Any]:
    """Return the active session with its items, or null. Auto-ends expired event sessions."""
    db, tid = ctx
    svc = BoothService(db, tid)

    # Auto-end if event session has expired
    svc.auto_end_if_expired()

    session = svc.get_active_session()
    if not session:
        return {"session": None}

    items = svc.list_items(session.session_id)
    return {
        "session": {
            "session_id": str(session.session_id),
            "name": session.name,
            "mode": session.mode or "regular",
            "duration_days": session.duration_days,
            "started_at": session.started_at.isoformat(),
            "ends_at": session.ends_at.isoformat() if session.ends_at else None,
            "items": [
                {
                    "item_id": str(i.item_id),
                    "variant_id": str(i.variant_id),
                    "product_name": i.product_name,
                    "variant_label": i.variant_label,
                    "booth_price": float(i.booth_price),
                    "stock_qty": i.stock_qty,
                    "sold_qty": i.sold_qty,
                    "remaining": i.remaining,
                }
                for i in items
            ],
        }
    }


@router.post("/{tenant_id}/api/checkout")
async def checkout(
    tenant_id: str,
    body: CheckoutRequest,
    ctx=Depends(_get_tenant_db),
) -> Dict[str, Any]:
    """
    Process a booth sale.

    Returns order details and, for Razorpay, a QR image URL.
    """
    db, tid = ctx
    svc = BoothService(db, tid)

    try:
        session_id = UUID(body.session_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid session_id")

    cart = []
    for item in body.items:
        try:
            cart.append(BoothItemInput(
                variant_id=UUID(item.variant_id),
                quantity=item.quantity,
                extra_charge=Decimal(str(item.extra_charge or 0)),
                extra_note=item.extra_note,
            ))
        except ValueError:
            raise HTTPException(status_code=400, detail=f"Invalid variant_id: {item.variant_id}")

    try:
        order = svc.checkout(
            session_id=session_id,
            cart=cart,
            payment_method=body.payment_method,
            customer_name=body.customer_name,
            gst_rate=Decimal(str(body.gst_rate or 0)),
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    response: Dict[str, Any] = {
        "order_id": str(order.order_id),
        "payment_id": str(order.payment_id),
        "subtotal": float(order.subtotal),
        "gst_amount": float(order.gst_amount),
        "gst_rate": float(order.gst_rate),
        "total_amount": float(order.total_amount),
        "payment_method": order.payment_method,
        "items": [
            {
                "product_name": i.product_name,
                "variant_label": i.variant_label,
                "quantity": i.quantity,
                "unit_price": float(i.unit_price),
                "extra_charge": float(i.extra_charge),
                "extra_note": i.extra_note or "",
                "line_total": float(i.line_total),
            }
            for i in order.items
        ],
        "razorpay_qr_url": None,
    }

    # For Razorpay: generate dynamic QR
    if body.payment_method == "razorpay":
        qr_url = await _create_razorpay_qr(db, tid, order.order_id, order.total_amount)
        response["razorpay_qr_url"] = qr_url

    return response


@router.get("/{tenant_id}/api/checkout/status/{order_id}")
async def payment_status(
    tenant_id: str,
    order_id: str,
    ctx=Depends(_get_tenant_db),
) -> Dict[str, str]:
    """Poll payment status for a Razorpay order."""
    db, tid = ctx
    try:
        oid = UUID(order_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid order_id")

    svc = BoothService(db, tid)
    status = svc.get_payment_status(oid)
    return {"status": status}


@router.get("/{tenant_id}/api/receipt/{order_id}", response_class=HTMLResponse)
async def receipt(
    tenant_id: str,
    order_id: str,
    request: Request,
    ctx=Depends(_get_tenant_db),
):
    """Serve a printable receipt page for an order."""
    db, tid = ctx
    try:
        oid = UUID(order_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid order_id")

    # Build receipt data
    order = db.query(Order).filter(
        Order.order_id == oid,
        Order.tenant_id == tid,
    ).first()
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")

    order_items = db.query(OrderItem).filter(OrderItem.order_id == oid).all()
    payment = db.query(Payment).filter(Payment.order_id == oid).first()

    # Get business name from registry
    from app.database import get_registry_db
    reg_db = next(get_registry_db())
    try:
        tenant = reg_db.query(Tenant).filter(Tenant.tenant_id == tid).first()
        business_name = tenant.business_name or "My Business" if tenant else "My Business"
    finally:
        reg_db.close()

    items = [
        {
            "name": oi.recipe_name,
            "qty": oi.quantity,
            "price": float(oi.selling_price),
            "total": float(oi.quantity * oi.selling_price),
        }
        for oi in order_items
    ]
    total = sum(i["total"] for i in items)

    return templates.TemplateResponse(
        request=request,
        name="receipt.html",
        context={
            "business_name": business_name,
            "order_id": str(oid)[:8].upper(),
            "date": order.created_at.strftime("%d %b %Y") if order.created_at else "",
            "items": items,
            "total": total,
            "payment_method": payment.method if payment else "—",
            "amount_paid": float(payment.amount) if payment else 0,
        },
    )


# ── Internal helpers ───────────────────────────────────────────────────────────

async def _create_razorpay_qr(
    db: Session,
    tenant_id: UUID,
    order_id: UUID,
    amount: Decimal,
) -> Optional[str]:
    """
    Create a Razorpay dynamic QR for the given order.
    Returns the QR image URL, or None if Razorpay is not configured.
    """
    from app.database import get_registry_db

    reg_db = next(get_registry_db())
    try:
        tenant = reg_db.query(Tenant).filter(Tenant.tenant_id == tenant_id).first()
        key_id = tenant.razorpay_key_id if tenant else None
        key_secret = tenant.razorpay_key_secret if tenant else None
    finally:
        reg_db.close()

    if not key_id or not key_secret:
        logger.warning(f"Razorpay not configured for tenant {tenant_id}")
        return None

    from app.booth.razorpay_client import RazorpayClient
    try:
        async with RazorpayClient(key_id, key_secret) as client:
            qr = await client.create_qr_code(
                amount_inr=amount,
                order_ref=str(order_id),
            )
        # Store QR ID on the payment for webhook lookup
        payment = db.query(Payment).filter(Payment.order_id == order_id).first()
        if payment:
            payment.razorpay_payment_id = qr.qr_id
            db.commit()
        return qr.image_url
    except ValueError as e:
        logger.error(f"Failed to create Razorpay QR: {e}")
        return None
