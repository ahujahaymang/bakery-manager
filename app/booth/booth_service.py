"""
Booth Service — core business logic for Exhibition Booth Mode.

No LLM calls. No HTTP. Pure CRUD against the existing DB schema.
All methods take a db Session and tenant_id; raise ValueError on bad input.

Responsibilities:
- Session lifecycle: start, end, get active, list past
- Session item management: add, remove, list
- Checkout: create Order + OrderItem + Payment atomically, increment sold_qty
- Reporting: session summary for agent queries ("how much did we sell at X?")
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime, date
from decimal import Decimal
from typing import Dict, List, Optional
from uuid import UUID

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models import (
    BoothSession,
    BoothSessionItem,
    Customer,
    Order,
    OrderItem,
    Payment,
    Product,
    ProductVariant,
)

logger = logging.getLogger(__name__)

# Walk-in customer name used when no customer name is provided at checkout
WALK_IN_NAME  = "Walk-in Customer"
WALK_IN_PHONE = "0000000000"


# ── Dataclasses ────────────────────────────────────────────────────────────────

@dataclass
class BoothItemInput:
    """One line in a checkout cart."""
    variant_id: UUID
    quantity: int


@dataclass
class BoothOrderItem:
    product_name: str
    variant_label: str
    quantity: int
    unit_price: Decimal
    line_total: Decimal


@dataclass
class BoothOrder:
    order_id: UUID
    total_amount: Decimal
    items: List[BoothOrderItem]
    payment_method: str
    payment_id: UUID
    razorpay_qr_url: Optional[str] = None   # set only for Razorpay payments


@dataclass
class SessionItemDetail:
    item_id: UUID
    variant_id: UUID
    product_name: str
    variant_label: str
    booth_price: Decimal
    stock_qty: Optional[int]
    sold_qty: int
    remaining: Optional[int]   # None = unlimited


@dataclass
class TopProduct:
    product_name: str
    variant_label: str
    units_sold: int
    revenue: Decimal


@dataclass
class SessionSummary:
    session_id: UUID
    name: str
    started_at: datetime
    ended_at: Optional[datetime]
    duration_minutes: Optional[int]
    total_orders: int
    total_revenue: Decimal
    items_sold: int
    top_products: List[TopProduct] = field(default_factory=list)


# ── Service ────────────────────────────────────────────────────────────────────

class BoothService:
    """
    All booth operations for a single tenant.

    Instantiate per-request with the tenant's DB session.
    """

    def __init__(self, db: Session, tenant_id: UUID):
        self.db = db
        self.tenant_id = tenant_id

    # ── Session lifecycle ──────────────────────────────────────────────────

    def start_session(
        self,
        name: str,
        mode: str = "regular",
        duration_days: int = None,
    ) -> BoothSession:
        """
        Create a new Register Mode session.

        mode="regular"  : open-ended, owner ends manually
        mode="event"    : fixed-duration; ends_at computed from duration_days
        """
        name = name.strip()
        if not name:
            raise ValueError("Session name is required")
        if mode not in ("regular", "event"):
            mode = "regular"

        active = self.get_active_session()
        if active:
            raise ValueError(
                f"Session '{active.name}' is already active. "
                "End it first before starting a new one."
            )

        now = datetime.utcnow()
        ends_at = None
        if mode == "event" and duration_days and duration_days > 0:
            from datetime import timedelta
            # ends at midnight on the last day (end of day N)
            ends_at = now.replace(hour=23, minute=59, second=59) + timedelta(days=duration_days - 1)

        session = BoothSession(
            tenant_id=self.tenant_id,
            name=name,
            mode=mode,
            duration_days=duration_days,
            started_at=now,
            ends_at=ends_at,
            created_at=now,
        )
        self.db.add(session)
        self.db.commit()
        self.db.refresh(session)
        logger.info(f"Register session started: {session.session_id} ({name}, mode={mode})")
        return session

    def auto_end_if_expired(self) -> bool:
        """
        Check if the active session has passed its ends_at time.
        If so, end it automatically. Returns True if ended.
        """
        session = self.get_active_session()
        if not session or not session.ends_at:
            return False
        if datetime.utcnow() >= session.ends_at:
            self.end_session(session.session_id)
            logger.info(f"Register session auto-ended: {session.session_id}")
            return True
        return False

    def end_session(self, session_id: UUID) -> BoothSession:
        """Close an active session. Returns the closed session."""
        session = self._get_session(session_id)
        if session.ended_at is not None:
            raise ValueError(f"Session '{session.name}' is already closed.")
        session.ended_at = datetime.utcnow()
        self.db.commit()
        self.db.refresh(session)
        logger.info(f"Booth session ended: {session_id}")
        return session

    def get_active_session(self) -> Optional[BoothSession]:
        """Return the currently active session, or None."""
        return (
            self.db.query(BoothSession)
            .filter(
                BoothSession.tenant_id == self.tenant_id,
                BoothSession.ended_at.is_(None),
            )
            .first()
        )

    def list_sessions(self) -> List[BoothSession]:
        """Return all sessions (active + closed) newest first."""
        return (
            self.db.query(BoothSession)
            .filter(BoothSession.tenant_id == self.tenant_id)
            .order_by(BoothSession.started_at.desc())
            .all()
        )

    # ── Session items ──────────────────────────────────────────────────────

    def add_item(
        self,
        session_id: UUID,
        variant_id: UUID,
        booth_price: Decimal,
        stock_qty: Optional[int],
    ) -> BoothSessionItem:
        """
        Add a product variant to a session.

        Raises ValueError if the variant is already in the session or
        the variant doesn't belong to this tenant.
        """
        session = self._get_session(session_id)
        if session.ended_at is not None:
            raise ValueError("Cannot add items to a closed session.")

        # Validate variant belongs to this tenant
        variant = self._get_variant(variant_id)

        # Prevent duplicates
        existing = (
            self.db.query(BoothSessionItem)
            .filter(
                BoothSessionItem.session_id == session_id,
                BoothSessionItem.variant_id == variant_id,
            )
            .first()
        )
        if existing:
            raise ValueError(
                f"'{variant.product.name} — {variant.size_label}' "
                "is already in this session."
            )

        if booth_price <= 0:
            raise ValueError("Booth price must be positive.")
        if stock_qty is not None and stock_qty < 0:
            raise ValueError("Stock quantity cannot be negative.")

        item = BoothSessionItem(
            session_id=session_id,
            variant_id=variant_id,
            booth_price=Decimal(str(booth_price)),
            stock_qty=stock_qty,
            sold_qty=0,
            created_at=datetime.utcnow(),
        )
        self.db.add(item)
        self.db.commit()
        self.db.refresh(item)
        return item

    def remove_item(self, session_id: UUID, variant_id: UUID) -> None:
        """Remove a product variant from a session."""
        session = self._get_session(session_id)
        if session.ended_at is not None:
            raise ValueError("Cannot modify a closed session.")

        item = (
            self.db.query(BoothSessionItem)
            .filter(
                BoothSessionItem.session_id == session_id,
                BoothSessionItem.variant_id == variant_id,
            )
            .first()
        )
        if not item:
            raise ValueError("Item not found in this session.")
        self.db.delete(item)
        self.db.commit()

    def list_items(self, session_id: UUID) -> List[SessionItemDetail]:
        """
        Return all items in a session with stock/sold info.
        Sorted by product category then product name.
        """
        items = (
            self.db.query(BoothSessionItem)
            .filter(BoothSessionItem.session_id == session_id)
            .all()
        )
        result = []
        for item in items:
            variant = self._get_variant(item.variant_id)
            remaining = (
                None if item.stock_qty is None
                else max(0, item.stock_qty - item.sold_qty)
            )
            result.append(SessionItemDetail(
                item_id=item.item_id,
                variant_id=item.variant_id,
                product_name=variant.product.name,
                variant_label=variant.size_label,
                booth_price=item.booth_price,
                stock_qty=item.stock_qty,
                sold_qty=item.sold_qty,
                remaining=remaining,
            ))
        # Sort by product name then variant label
        result.sort(key=lambda x: (x.product_name.lower(), x.variant_label.lower()))
        return result

    # ── Checkout ───────────────────────────────────────────────────────────

    def checkout(
        self,
        session_id: UUID,
        cart: List[BoothItemInput],
        payment_method: str,          # "cash" | "upi" | "razorpay"
        customer_name: Optional[str] = None,
    ) -> BoothOrder:
        """
        Process a booth sale atomically:
          1. Validate stock for all items
          2. Resolve or create walk-in customer
          3. Create Order (status=delivered, booth_session_id set)
          4. Create OrderItems from variant prices
          5. Create Payment (status=completed for cash/upi, pending for razorpay)
          6. Increment sold_qty on each BoothSessionItem

        Raises ValueError on stock shortage or invalid input.
        """
        if not cart:
            raise ValueError("Cart is empty.")

        session = self._get_session(session_id)
        if session.ended_at is not None:
            raise ValueError("This booth session has ended.")

        payment_method = payment_method.lower().strip()
        if payment_method not in ("cash", "upi", "razorpay"):
            raise ValueError("Payment method must be 'cash', 'upi', or 'razorpay'.")

        # ── Validate and resolve all cart items ────────────────────────────
        resolved: List[Dict] = []
        for cart_item in cart:
            if cart_item.quantity <= 0:
                raise ValueError("Quantity must be at least 1.")

            # Find the BoothSessionItem for this variant
            session_item = (
                self.db.query(BoothSessionItem)
                .filter(
                    BoothSessionItem.session_id == session_id,
                    BoothSessionItem.variant_id == cart_item.variant_id,
                )
                .first()
            )
            if not session_item:
                raise ValueError(
                    f"Variant {cart_item.variant_id} is not in this session."
                )

            # Stock check
            if session_item.stock_qty is not None:
                available = session_item.stock_qty - session_item.sold_qty
                if cart_item.quantity > available:
                    variant = self._get_variant(cart_item.variant_id)
                    raise ValueError(
                        f"Not enough stock for '{variant.product.name} — "
                        f"{variant.size_label}'. "
                        f"Available: {available}, requested: {cart_item.quantity}."
                    )

            variant = self._get_variant(cart_item.variant_id)
            resolved.append({
                "session_item": session_item,
                "variant": variant,
                "quantity": cart_item.quantity,
                "unit_price": session_item.booth_price,
                "line_total": session_item.booth_price * cart_item.quantity,
            })

        total_amount = sum(r["line_total"] for r in resolved)

        # ── Resolve customer ───────────────────────────────────────────────
        customer = self._resolve_customer(customer_name)

        # ── Create Order ───────────────────────────────────────────────────
        order = Order(
            tenant_id=self.tenant_id,
            customer_id=customer.customer_id,
            delivery_date=date.today(),
            delivery_address=None,
            status="delivered",          # booth sales are immediate
            booth_session_id=session_id,
        )
        self.db.add(order)
        self.db.flush()

        # ── Create OrderItems ──────────────────────────────────────────────
        order_items_out: List[BoothOrderItem] = []
        for r in resolved:
            variant = r["variant"]
            oi = OrderItem(
                order_id=order.order_id,
                recipe_id=None,
                recipe_name=f"{variant.product.name} — {variant.size_label}",
                quantity=r["quantity"],
                selling_price=r["unit_price"],
                customization_charge=Decimal("0"),
            )
            self.db.add(oi)
            order_items_out.append(BoothOrderItem(
                product_name=variant.product.name,
                variant_label=variant.size_label,
                quantity=r["quantity"],
                unit_price=r["unit_price"],
                line_total=r["line_total"],
            ))

        # ── Create Payment ─────────────────────────────────────────────────
        payment_status = "pending" if payment_method == "razorpay" else "completed"
        payment = Payment(
            tenant_id=self.tenant_id,
            order_id=order.order_id,
            amount=total_amount,
            method=payment_method.capitalize(),
            status=payment_status,
        )
        self.db.add(payment)
        self.db.flush()

        # ── Increment sold_qty ─────────────────────────────────────────────
        for r in resolved:
            r["session_item"].sold_qty += r["quantity"]

        self.db.commit()
        self.db.refresh(order)
        self.db.refresh(payment)

        logger.info(
            f"Booth checkout: order={order.order_id} "
            f"total=₹{total_amount} method={payment_method}"
        )

        return BoothOrder(
            order_id=order.order_id,
            total_amount=total_amount,
            items=order_items_out,
            payment_method=payment_method,
            payment_id=payment.payment_id,
        )

    def confirm_razorpay_payment(
        self, razorpay_payment_id: str, order_id: UUID
    ) -> Payment:
        """
        Mark a Razorpay payment as completed after webhook confirmation.
        Called by the Razorpay webhook handler.
        """
        payment = (
            self.db.query(Payment)
            .filter(
                Payment.order_id == order_id,
                Payment.tenant_id == self.tenant_id,
            )
            .first()
        )
        if not payment:
            raise ValueError(f"Payment not found for order {order_id}")

        payment.razorpay_payment_id = razorpay_payment_id
        payment.status = "completed"
        self.db.commit()
        self.db.refresh(payment)
        return payment

    def get_payment_status(self, order_id: UUID) -> str:
        """Return payment status for an order: 'pending' | 'completed' | 'not_found'."""
        payment = (
            self.db.query(Payment)
            .filter(
                Payment.order_id == order_id,
                Payment.tenant_id == self.tenant_id,
            )
            .first()
        )
        return payment.status if payment else "not_found"

    # ── Reporting ──────────────────────────────────────────────────────────

    def get_session_summary(self, session_id: UUID) -> SessionSummary:
        """
        Build a summary of a booth session for agent reporting.
        Includes total revenue, items sold, and top products.
        """
        session = self._get_session(session_id)

        # All orders for this session
        orders = (
            self.db.query(Order)
            .filter(
                Order.booth_session_id == session_id,
                Order.tenant_id == self.tenant_id,
            )
            .all()
        )

        total_revenue = Decimal("0")
        items_sold = 0
        product_stats: Dict[str, Dict] = {}

        for order in orders:
            order_items = (
                self.db.query(OrderItem)
                .filter(OrderItem.order_id == order.order_id)
                .all()
            )
            for oi in order_items:
                line = oi.quantity * oi.selling_price
                total_revenue += line
                items_sold += oi.quantity
                key = oi.recipe_name
                if key not in product_stats:
                    product_stats[key] = {"units": 0, "revenue": Decimal("0")}
                product_stats[key]["units"] += oi.quantity
                product_stats[key]["revenue"] += line

        # Top 5 by revenue
        top = sorted(
            product_stats.items(),
            key=lambda x: x[1]["revenue"],
            reverse=True,
        )[:5]
        top_products = [
            TopProduct(
                product_name=name,
                variant_label="",
                units_sold=stats["units"],
                revenue=stats["revenue"],
            )
            for name, stats in top
        ]

        duration = None
        if session.ended_at:
            duration = int(
                (session.ended_at - session.started_at).total_seconds() / 60
            )

        return SessionSummary(
            session_id=session.session_id,
            name=session.name,
            started_at=session.started_at,
            ended_at=session.ended_at,
            duration_minutes=duration,
            total_orders=len(orders),
            total_revenue=total_revenue,
            items_sold=items_sold,
            top_products=top_products,
        )

    # ── Internal helpers ───────────────────────────────────────────────────

    def _get_session(self, session_id: UUID) -> BoothSession:
        session = (
            self.db.query(BoothSession)
            .filter(
                BoothSession.session_id == session_id,
                BoothSession.tenant_id == self.tenant_id,
            )
            .first()
        )
        if not session:
            raise ValueError(f"Booth session {session_id} not found.")
        return session

    def _get_variant(self, variant_id: UUID) -> ProductVariant:
        variant = (
            self.db.query(ProductVariant)
            .filter(ProductVariant.variant_id == variant_id)
            .first()
        )
        if not variant:
            raise ValueError(f"Product variant {variant_id} not found.")
        return variant

    def _resolve_customer(self, customer_name: Optional[str]) -> Customer:
        """
        Find or create a customer for a booth sale.
        If no name given, use the shared Walk-in Customer record.
        """
        name = (customer_name or "").strip() or WALK_IN_NAME
        phone = WALK_IN_PHONE if name == WALK_IN_NAME else "0000000000"

        # Try to find by name (case-insensitive)
        customer = (
            self.db.query(Customer)
            .filter(
                Customer.tenant_id == self.tenant_id,
                func.lower(Customer.name) == name.lower(),
            )
            .first()
        )
        if customer:
            return customer

        # Create new customer
        # For walk-in, reuse the single walk-in record (unique phone constraint)
        existing_walkin = (
            self.db.query(Customer)
            .filter(
                Customer.tenant_id == self.tenant_id,
                Customer.phone == WALK_IN_PHONE,
            )
            .first()
        )
        if existing_walkin:
            return existing_walkin

        customer = Customer(
            tenant_id=self.tenant_id,
            name=name,
            phone=phone,
        )
        self.db.add(customer)
        self.db.flush()
        return customer
