"""
Invoice Service — generates PDF invoices from order data.

Uses ReportLab canvas (direct drawing) for precise, overlap-free layout.
"""

import io
import logging
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import List
from uuid import UUID

logger = logging.getLogger(__name__)


@dataclass
class InvoiceItem:
    description: str
    quantity: int
    unit_price: Decimal
    total: Decimal


@dataclass
class InvoiceData:
    invoice_number: str
    issue_date: date
    business_name: str
    customer_name: str
    customer_phone: str
    delivery_date: date
    items: List[InvoiceItem]
    subtotal: Decimal
    amount_paid: Decimal
    amount_due: Decimal
    currency: str = "Rs."


class InvoiceService:
    """Generates PDF invoices using ReportLab canvas for precise layout."""

    # Colours
    COLOR_PRIMARY = (0.10, 0.10, 0.18)   # dark navy
    COLOR_ACCENT  = (0.91, 0.27, 0.38)   # red-pink
    COLOR_LIGHT   = (0.96, 0.96, 0.96)   # light grey row
    COLOR_WHITE   = (1.0,  1.0,  1.0)
    COLOR_GREY    = (0.53, 0.53, 0.53)

    def generate(self, data: InvoiceData) -> bytes:
        from reportlab.pdfgen import canvas
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.units import mm

        W, H = A4          # 595 x 842 pt
        margin = 20 * mm   # ~57 pt

        buf = io.BytesIO()
        c = canvas.Canvas(buf, pagesize=A4)

        y = H - margin     # current y, drawing top-down

        # ── Header bar ────────────────────────────────────────────────────
        bar_h = 22 * mm
        self._rect(c, 0, H - bar_h, W, bar_h, self.COLOR_PRIMARY)

        # Business name (left)
        c.setFillColorRGB(*self.COLOR_WHITE)
        c.setFont("Helvetica-Bold", 18)
        c.drawString(margin, H - bar_h + 7 * mm, data.business_name)

        # INVOICE label (right)
        c.setFont("Helvetica-Bold", 22)
        c.setFillColorRGB(*self.COLOR_ACCENT)
        c.drawRightString(W - margin, H - bar_h + 7 * mm, "INVOICE")

        y = H - bar_h - 10 * mm

        # ── Two-column meta block ─────────────────────────────────────────
        col_left  = margin
        col_right = W / 2 + 10 * mm

        # Left: Bill To
        self._label(c, col_left, y, "BILL TO")
        y -= 5 * mm
        self._value(c, col_left, y, data.customer_name, size=12)
        y -= 5 * mm
        self._body(c, col_left, y, data.customer_phone)

        # Right: invoice meta (aligned to right column, same y as start)
        meta_y = H - bar_h - 10 * mm
        pairs = [
            ("Invoice #",      data.invoice_number),
            ("Issue Date",     str(data.issue_date)),
            ("Delivery Date",  str(data.delivery_date)),
        ]
        for label, val in pairs:
            self._label(c, col_right, meta_y, label)
            self._value(c, col_right + 28 * mm, meta_y, val)
            meta_y -= 6 * mm

        y -= 10 * mm

        # ── Divider ───────────────────────────────────────────────────────
        self._hline(c, margin, W - margin, y, self.COLOR_ACCENT, 1.5)
        y -= 8 * mm

        # ── Items table header ────────────────────────────────────────────
        row_h = 8 * mm
        col_desc  = margin
        col_qty   = W - margin - 60 * mm
        col_price = W - margin - 35 * mm
        col_total = W - margin - 10 * mm

        # Header background
        self._rect(c, margin, y - row_h, W - 2 * margin, row_h, self.COLOR_PRIMARY)
        c.setFillColorRGB(*self.COLOR_WHITE)
        c.setFont("Helvetica-Bold", 9)
        pad = 2 * mm
        c.drawString(col_desc + pad,  y - row_h + pad, "ITEM")
        c.drawRightString(col_qty,    y - row_h + pad, "QTY")
        c.drawRightString(col_price,  y - row_h + pad, "UNIT PRICE")
        c.drawRightString(col_total,  y - row_h + pad, "TOTAL")
        y -= row_h

        # ── Item rows ─────────────────────────────────────────────────────
        for i, item in enumerate(data.items):
            bg = self.COLOR_LIGHT if i % 2 == 0 else self.COLOR_WHITE
            self._rect(c, margin, y - row_h, W - 2 * margin, row_h, bg)

            c.setFillColorRGB(*self.COLOR_PRIMARY)
            c.setFont("Helvetica", 10)
            c.drawString(col_desc + pad,  y - row_h + pad, item.description)
            c.drawRightString(col_qty,    y - row_h + pad, str(item.quantity))
            c.drawRightString(col_price,  y - row_h + pad, f"{data.currency}{item.unit_price:.2f}")
            c.drawRightString(col_total,  y - row_h + pad, f"{data.currency}{item.total:.2f}")
            y -= row_h

        y -= 4 * mm

        # ── Totals block (right-aligned) ──────────────────────────────────
        totals_x = W / 2
        totals_w = W - margin - totals_x

        def total_row(label, amount, bold=False, highlight=False):
            nonlocal y
            row_bg = self.COLOR_ACCENT if highlight else None
            if row_bg:
                self._rect(c, totals_x, y - row_h, totals_w, row_h, row_bg)
                c.setFillColorRGB(*self.COLOR_WHITE)
            else:
                c.setFillColorRGB(*self.COLOR_PRIMARY)

            font = "Helvetica-Bold" if bold else "Helvetica"
            size = 11 if highlight else 10
            c.setFont(font, size)
            c.drawString(totals_x + pad, y - row_h + pad, label)
            c.drawRightString(W - margin, y - row_h + pad, f"{data.currency}{amount:.2f}")
            y -= row_h

        total_row("Subtotal",     data.subtotal)
        total_row("Amount Paid",  data.amount_paid)
        total_row("AMOUNT DUE",   data.amount_due, bold=True, highlight=True)

        # ── Footer ────────────────────────────────────────────────────────
        y -= 12 * mm
        self._hline(c, margin, W - margin, y, self.COLOR_GREY, 0.5)
        y -= 6 * mm
        c.setFont("Helvetica", 9)
        c.setFillColorRGB(*self.COLOR_GREY)
        c.drawCentredString(W / 2, y, "Thank you for your business!")

        c.save()
        return buf.getvalue()

    # ── Drawing helpers ────────────────────────────────────────────────────

    def _rect(self, c, x, y, w, h, color):
        c.setFillColorRGB(*color)
        c.rect(x, y, w, h, fill=1, stroke=0)

    def _hline(self, c, x1, x2, y, color, width=0.5):
        c.setStrokeColorRGB(*color)
        c.setLineWidth(width)
        c.line(x1, y, x2, y)

    def _label(self, c, x, y, text):
        c.setFont("Helvetica", 8)
        c.setFillColorRGB(*self.COLOR_GREY)
        c.drawString(x, y, text)

    def _value(self, c, x, y, text, size=10):
        c.setFont("Helvetica-Bold", size)
        c.setFillColorRGB(*self.COLOR_PRIMARY)
        c.drawString(x, y, text)

    def _body(self, c, x, y, text):
        c.setFont("Helvetica", 10)
        c.setFillColorRGB(*self.COLOR_PRIMARY)
        c.drawString(x, y, text)

    # ── Data builder ──────────────────────────────────────────────────────

    def build_invoice_data(
        self,
        db,
        tenant_id: UUID,
        order_id: UUID,
        business_name: str,
        currency: str = "Rs.",
    ) -> InvoiceData:
        from app.models import Order, OrderItem, Customer, Payment

        order = db.query(Order).filter(
            Order.order_id == order_id,
            Order.tenant_id == tenant_id,
        ).first()
        if not order:
            raise ValueError("Order not found")

        customer = db.query(Customer).filter(
            Customer.customer_id == order.customer_id
        ).first()

        order_items = db.query(OrderItem).filter(
            OrderItem.order_id == order_id
        ).all()

        payments = db.query(Payment).filter(
            Payment.order_id == order_id
        ).all()

        subtotal = sum(
            i.quantity * (i.selling_price + (i.customization_charge or Decimal("0")))
            for i in order_items
        )
        amount_paid  = sum(p.amount for p in payments)
        amount_due   = subtotal - amount_paid

        items = [
            InvoiceItem(
                description=item.recipe_name,
                quantity=item.quantity,
                # Combined price: customization folded into unit price, not shown separately
                unit_price=item.selling_price + (item.customization_charge or Decimal("0")),
                total=item.quantity * (item.selling_price + (item.customization_charge or Decimal("0"))),
            )
            for item in order_items
        ]

        return InvoiceData(
            invoice_number=f"INV-{str(order_id)[:8].upper()}",
            issue_date=date.today(),
            business_name=business_name or "My Business",
            customer_name=customer.name if customer else "Customer",
            customer_phone=customer.phone if customer else "",
            delivery_date=order.delivery_date,
            items=items,
            subtotal=subtotal,
            amount_paid=amount_paid,
            amount_due=amount_due,
            currency=currency,
        )
