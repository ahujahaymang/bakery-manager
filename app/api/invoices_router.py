"""
Invoices domain router for the app-first REST API (``/api/v1/invoices``).

A thin HTTP adapter over the existing, unchanged :class:`InvoiceService`
(design §"No business-logic rewrite"). It exposes a single endpoint, available
to **Owner and Staff** (an invoice carries sale/receivable figures only — no
cost/profit data — so authentication is the only gate, matching the Sell
surface's invoice endpoint):

- ``POST /api/v1/invoices`` — generate a PDF invoice for an existing order.
  Reuses ``InvoiceService.build_invoice_data`` to assemble the invoice from the
  order's line items and ``InvoiceService.generate`` to render the PDF. The
  invoice contains the business name, customer details, an itemised list of
  ordered items with quantities and unit prices, a subtotal, applicable taxes,
  and the total, with every monetary value shown in the tenant's configured
  currency (Req 13.1, 13.3).

Behaviour mandated by Requirement 13:

- **Optional GST breakdown (Req 13.2, 13.7).** When a tax rate/label is
  configured for the request, ``build_invoice_data`` computes the tax amount and
  the rendered PDF shows the GST breakdown (label, rate %, tax amount). When no
  tax rate is configured (rate 0/absent), the invoice is generated without a GST
  breakdown.
- **Order not found (Req 13.5).** An invoice request for an order that does not
  exist is rejected with a ``404 not_found`` (translated from the service
  ``ValueError``) and no PDF is generated.
- **Any delivery date (Req 13.4).** An invoice is generated for an order
  regardless of whether its delivery date is in the past, today, or the future,
  so advance/made-to-order orders can be invoiced at order time.

The tenant is taken exclusively from the authenticated principal via
:func:`get_tenant_db_for_user`, so the invoice is built strictly from the
caller's tenant data (Req 19.2, 19.6). The tenant's business name and currency
are resolved from the registry via the shared helper reused from the Sell
router, avoiding duplicated tenant-context logic.

_Requirements: 13.1, 13.2, 13.3, 13.4, 13.5, 13.6, 13.7_
"""

from __future__ import annotations

import base64
import logging
from decimal import Decimal
from typing import Any, Dict, Optional
from uuid import UUID

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api import errors
from app.api.deps import AuthedUser, get_current_user, get_tenant_db_for_user
from app.api.schemas import serialize_invoice
from app.api.sell_router import _tenant_invoice_context
from app.services.invoice_service import InvoiceService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/invoices", tags=["invoices"])


# ── Request schema ──────────────────────────────────────────────────────────────

class InvoiceGenerateRequest(BaseModel):
    """Body for ``POST /api/v1/invoices``.

    ``order_id`` identifies the existing order to invoice. ``tax_rate`` and
    ``tax_label`` are the tenant's configured GST settings for this invoice:
    when ``tax_rate`` is greater than zero a GST breakdown is included
    (Req 13.2); when it is zero/absent the invoice is generated without a GST
    breakdown (Req 13.7). ``tax_label`` is optional — the service derives a
    ``GST (<rate>%)`` label when a rate is configured but no label is given.
    """

    order_id: str = Field(min_length=1)
    tax_rate: Decimal = Field(default=Decimal("0"), ge=0, le=Decimal("100"))
    tax_label: Optional[str] = None


# ── Helpers ──────────────────────────────────────────────────────────────────────

def _parse_uuid(value: str, field: str) -> UUID:
    """Parse a UUID string, raising a 400 validation error on failure."""
    try:
        return UUID(str(value))
    except (ValueError, AttributeError, TypeError):
        raise errors.ValidationError(detail=f"Invalid {field}", field=field)


# ── Routes ────────────────────────────────────────────────────────────────────────

@router.post("")
def generate_invoice(
    body: InvoiceGenerateRequest,
    user: AuthedUser = Depends(get_current_user),
    db: Session = Depends(get_tenant_db_for_user),
) -> Dict[str, Any]:
    """Generate a PDF invoice for an existing order (Owner+Staff).

    Reuses ``InvoiceService.build_invoice_data``/``generate`` unchanged. The
    business name and tenant currency are resolved from the registry, so every
    monetary value on the invoice is rendered in the tenant's configured
    currency (Req 13.1, 13.3). A configured tax rate/label produces a GST
    breakdown; otherwise the invoice is generated without one (Req 13.2, 13.7).

    An unknown order surfaces the service ``ValueError`` as ``404 not_found``
    before any PDF is generated (Req 13.5). An invoice is generated regardless
    of the order's delivery date — past, today, or future — so advance orders
    can be invoiced at order time (Req 13.4). The PDF is returned base64-encoded
    alongside the serialized invoice metadata, mirroring the Sell invoice
    endpoint.
    """
    order_id = _parse_uuid(body.order_id, "order_id")

    business_name, currency = _tenant_invoice_context(user.tenant_id)

    svc = InvoiceService()

    # Build the invoice data first. This reads the order for the caller's tenant
    # and raises ValueError("Order not found") for an unknown order, which
    # map_service_errors translates to 404 — no PDF is generated (Req 13.5).
    with errors.map_service_errors():
        data = svc.build_invoice_data(
            db,
            user.tenant_id,
            order_id,
            business_name,
            currency,
            tax_rate=body.tax_rate or Decimal("0"),
            tax_label=(body.tax_label or "").strip(),
        )

    # Invoices may be generated for an order regardless of its delivery date.
    # Home bakers routinely invoice advance/made-to-order orders whose delivery
    # is still in the future (e.g. to collect an advance), so no delivery-date
    # gate is applied here (Req 13.4).
    pdf_bytes = svc.generate(data)

    return {
        "filename": f"invoice_{data.invoice_number}.pdf",
        "content_type": "application/pdf",
        "data": base64.b64encode(pdf_bytes).decode(),
        "invoice": serialize_invoice(data, user),
    }
