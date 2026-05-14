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
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.booth.booth_service import BoothItemInput, BoothService
from app.database import get_db
from app.models import Order, OrderItem, Payment, Tenant

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/booth", tags=["booth"])

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


class CheckoutRequest(BaseModel):
    session_id: str
    items: List[CheckoutItemSchema]
    payment_method: Literal["cash", "upi", "razorpay"]
    customer_name: Optional[str] = None


# ── Routes ─────────────────────────────────────────────────────────────────────

@router.get("/{tenant_id}", response_class=HTMLResponse)
async def serve_booth(tenant_id: str, request: Request):
    """Serve the single-page booth app."""
    # Validate tenant exists (lightweight check)
    try:
        UUID(tenant_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="Invalid tenant ID")

    return templates.TemplateResponse(
        request=request,
        name="booth.html",
        context={"tenant_id": tenant_id},
    )


@router.get("/{tenant_id}/api/session/active")
async def get_active_session(
    tenant_id: str,
    ctx=Depends(_get_tenant_db),
) -> Dict[str, Any]:
    """Return the active session with its items, or null."""
    db, tid = ctx
    svc = BoothService(db, tid)
    session = svc.get_active_session()
    if not session:
        return {"session": None}

    items = svc.list_items(session.session_id)
    return {
        "session": {
            "session_id": str(session.session_id),
            "name": session.name,
            "started_at": session.started_at.isoformat(),
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
            ))
        except ValueError:
            raise HTTPException(status_code=400, detail=f"Invalid variant_id: {item.variant_id}")

    try:
        order = svc.checkout(
            session_id=session_id,
            cart=cart,
            payment_method=body.payment_method,
            customer_name=body.customer_name,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    response: Dict[str, Any] = {
        "order_id": str(order.order_id),
        "payment_id": str(order.payment_id),
        "total_amount": float(order.total_amount),
        "payment_method": order.payment_method,
        "items": [
            {
                "product_name": i.product_name,
                "variant_label": i.variant_label,
                "quantity": i.quantity,
                "unit_price": float(i.unit_price),
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
