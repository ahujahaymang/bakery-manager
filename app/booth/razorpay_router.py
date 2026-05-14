"""
Razorpay webhook router — POST /razorpay/webhook

Receives payment.captured events from Razorpay and marks the
corresponding booth payment as completed.

Signature verification uses the tenant's own key_secret (looked up
via the razorpay_payment_id stored on the Payment record).
"""

import logging
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

from app.database import get_db, get_registry_db
from app.models import Payment, Tenant

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/razorpay", tags=["razorpay"])


@router.post("/webhook")
async def razorpay_webhook(request: Request) -> JSONResponse:
    """
    Handle Razorpay payment.captured webhook.

    Flow:
    1. Read raw body (needed for signature verification)
    2. Parse event — only handle payment.captured
    3. Find Payment by razorpay_payment_id (the QR ID we stored at checkout)
    4. Look up tenant, verify HMAC signature with their key_secret
    5. Mark payment as completed
    6. Return 200 immediately (Razorpay retries on non-200)
    """
    body = await request.body()
    signature = request.headers.get("X-Razorpay-Signature", "")

    # Parse JSON body
    try:
        import json
        payload = json.loads(body)
    except Exception:
        logger.warning("Razorpay webhook: invalid JSON body")
        raise HTTPException(status_code=400, detail="Invalid JSON")

    event = payload.get("event", "")
    logger.info(f"Razorpay webhook received: event={event}")

    # Only process payment.captured
    if event != "payment.captured":
        return JSONResponse({"status": "ignored", "event": event})

    # Extract payment details
    try:
        payment_entity = payload["payload"]["payment"]["entity"]
        razorpay_payment_id = payment_entity["id"]
        # For QR payments, the QR ID is in the notes or acquirer_data
        # Razorpay sends the QR code ID in acquirer_data.rrn or notes.order_ref
        notes = payment_entity.get("notes", {})
        order_ref = notes.get("order_ref")  # our internal order UUID
    except (KeyError, TypeError) as e:
        logger.warning(f"Razorpay webhook: missing fields — {e}")
        raise HTTPException(status_code=400, detail="Missing payment fields")

    if not order_ref:
        logger.warning("Razorpay webhook: no order_ref in notes")
        return JSONResponse({"status": "no_order_ref"})

    # Find the order and its tenant
    try:
        order_id = UUID(order_ref)
    except ValueError:
        logger.warning(f"Razorpay webhook: invalid order_ref={order_ref}")
        return JSONResponse({"status": "invalid_order_ref"})

    # Find payment across all tenant DBs
    # We need to find which tenant owns this order
    reg_db = next(get_registry_db())
    try:
        tenants = reg_db.query(Tenant).filter(
            Tenant.razorpay_key_secret.isnot(None)
        ).all()
    finally:
        reg_db.close()

    for tenant in tenants:
        tenant_id = tenant.tenant_id
        db = next(get_db(tenant_id))
        try:
            payment = db.query(Payment).filter(
                Payment.order_id == order_id,
                Payment.tenant_id == tenant_id,
            ).first()

            if not payment:
                continue

            # Verify signature with this tenant's key_secret
            from app.booth.razorpay_client import RazorpayClient
            client = RazorpayClient(
                tenant.razorpay_key_id,
                tenant.razorpay_key_secret,
            )
            # Razorpay webhook secret may differ from API secret.
            # For simplicity in Phase 1 we use the key_secret.
            # In Phase 2, store a separate webhook_secret per tenant.
            if not client.verify_webhook_signature(
                body, signature, tenant.razorpay_key_secret
            ):
                logger.warning(
                    f"Razorpay webhook: signature mismatch for tenant {tenant_id}"
                )
                raise HTTPException(status_code=403, detail="Invalid signature")

            # Mark payment completed
            from app.booth.booth_service import BoothService
            svc = BoothService(db, tenant_id)
            svc.confirm_razorpay_payment(razorpay_payment_id, order_id)

            logger.info(
                f"Razorpay payment confirmed: order={order_id} "
                f"payment={razorpay_payment_id}"
            )
            return JSONResponse({"status": "ok"})

        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Razorpay webhook error for tenant {tenant_id}: {e}", exc_info=True)
        finally:
            db.close()

    logger.warning(f"Razorpay webhook: order {order_id} not found in any tenant DB")
    # Return 200 anyway — Razorpay should not retry for unknown orders
    return JSONResponse({"status": "order_not_found"})
