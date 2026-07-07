"""
Unit tests for outbound notification triggers (app-first pivot, task 17.1).

Covers the four senders (order alert, Instagram-order alert, expiry warning,
daily summary), delivery over the tenant's configured messaging_platform,
bounded retry with failure recording, and the once-per-calendar-day gating.
External send paths are stubbed; no real Telegram/WhatsApp transport is used.
"""

import asyncio
from datetime import date, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

import app.models  # noqa: F401 — register tables
from app.models import Base, Tenant, Customer, Order, OrderItem, Payment, Recipe
from app.notifications import (
    Notifier,
    NotificationKind,
    DailySummaryData,
    InMemorySentLedger,
    compute_daily_summary,
    expiry_warning_due,
)


# ── send-path stubs ────────────────────────────────────────────────────────────

class RecordingSender:
    """Records (destination, message) and always succeeds."""

    def __init__(self):
        self.sent = []

    async def __call__(self, destination, message):
        self.sent.append((destination, message))


class FailingSender:
    """Raises up to ``fail_times`` times, then succeeds. Counts total calls."""

    def __init__(self, fail_times):
        self.fail_times = fail_times
        self.calls = 0

    async def __call__(self, destination, message):
        self.calls += 1
        if self.calls <= self.fail_times:
            raise RuntimeError(f"transport down (call {self.calls})")


class FakeTenant:
    def __init__(self, *, platform="telegram", chat_id="chat_1", expires_at=None):
        self.tenant_id = uuid4()
        self.chat_id = chat_id
        self.business_name = "Sweet Treats"
        self.messaging_platform = platform
        self.subscription_status = "active"
        self.subscription_expires_at = expires_at


def _run(coro):
    return asyncio.run(coro)


# ── order alert ─────────────────────────────────────────────────────────────

def test_order_alert_contains_required_fields_and_delivers():
    sender = RecordingSender()
    tenant = FakeTenant(platform="telegram")
    notifier = Notifier({"telegram": sender})

    items = [{"name": "Chocolate Cake", "quantity": 2, "price": 500}]
    result = _run(
        notifier.send_order_alert(tenant, "ORD-123", "Asha", items, 1000)
    )

    assert result.delivered is True
    assert result.attempts == 1
    assert len(sender.sent) == 1
    dest, msg = sender.sent[0]
    assert dest == "chat_1"
    # Req 17.1/17.3: order id, customer, items, total present.
    assert "ORD-123" in msg
    assert "Asha" in msg
    assert "Chocolate Cake" in msg
    assert "1000" in msg


def test_delivery_uses_configured_messaging_platform():
    telegram = RecordingSender()
    whatsapp = RecordingSender()
    tenant = FakeTenant(platform="whatsapp", chat_id="9199999")
    notifier = Notifier({"telegram": telegram, "whatsapp": whatsapp})

    _run(notifier.send_order_alert(tenant, "ORD-1", "Bob", [], 10))

    assert whatsapp.sent and not telegram.sent  # Req 17.6


# ── instagram alert ───────────────────────────────────────────────────────────

def test_instagram_alert_contains_handle_and_details():
    sender = RecordingSender()
    tenant = FakeTenant()
    notifier = Notifier({"telegram": sender})

    items = [{"name": "Cupcakes", "quantity": 12, "price": None}]
    result = _run(
        notifier.send_instagram_order_alert(
            tenant, "@bakelover", items, delivery_date="2025-01-10"
        )
    )

    assert result.delivered is True
    _, msg = sender.sent[0]
    assert "bakelover" in msg
    assert "Cupcakes" in msg


# ── retry + failure recording (Req 17.7) ──────────────────────────────────────

def test_retries_up_to_three_times_then_succeeds():
    sender = FailingSender(fail_times=2)  # 2 failures, 3rd succeeds
    tenant = FakeTenant()
    notifier = Notifier({"telegram": sender})

    result = _run(notifier.send_order_alert(tenant, "ORD-1", "A", [], 1))

    assert result.delivered is True
    assert result.attempts == 3
    assert sender.calls == 3


def test_records_failure_after_three_exhausted_attempts():
    sender = FailingSender(fail_times=99)  # always fails
    recorded = []
    tenant = FakeTenant()
    notifier = Notifier({"telegram": sender}, failure_recorder=recorded.append)

    result = _run(notifier.send_order_alert(tenant, "ORD-1", "A", [], 1))

    assert result.delivered is False
    assert result.attempts == 3  # never more than 3 (Req 17.7)
    assert sender.calls == 3
    assert len(recorded) == 1
    assert recorded[0].kind == NotificationKind.ORDER_ALERT
    assert recorded[0].attempts == 3


def test_missing_send_path_records_failure_without_attempts():
    recorded = []
    tenant = FakeTenant(platform="whatsapp")
    notifier = Notifier({"telegram": RecordingSender()}, failure_recorder=recorded.append)

    result = _run(notifier.send_order_alert(tenant, "ORD-1", "A", [], 1))

    assert result.delivered is False
    assert result.attempts == 0
    assert len(recorded) == 1


# ── expiry warning window + once-per-day (Req 17.2) ────────────────────────────

def test_expiry_warning_due_only_inside_seven_day_window():
    today = date(2025, 1, 1)
    inside = FakeTenant(expires_at=datetime(2025, 1, 5))   # 4 days out
    boundary = FakeTenant(expires_at=datetime(2025, 1, 8)) # 7 days out
    outside = FakeTenant(expires_at=datetime(2025, 1, 10)) # 9 days out
    already = FakeTenant(expires_at=datetime(2024, 12, 31))  # already expired
    none = FakeTenant(expires_at=None)

    assert expiry_warning_due(inside, today) == 4
    assert expiry_warning_due(boundary, today) == 7
    assert expiry_warning_due(outside, today) is None
    assert expiry_warning_due(already, today) is None
    assert expiry_warning_due(none, today) is None


def test_expiry_warning_sent_once_per_calendar_day():
    sender = RecordingSender()
    tenant = FakeTenant(expires_at=datetime(2025, 1, 5))
    notifier = Notifier({"telegram": sender})
    day = date(2025, 1, 1)

    first = _run(notifier.send_expiry_warning(tenant, today=day))
    second = _run(notifier.send_expiry_warning(tenant, today=day))
    next_day = _run(notifier.send_expiry_warning(tenant, today=date(2025, 1, 2)))

    assert first is not None and first.delivered is True
    assert second is None  # suppressed same day (Req 17.2)
    assert next_day is not None and next_day.delivered is True
    assert len(sender.sent) == 2
    # message carries expiry date + days remaining
    _, msg = sender.sent[0]
    assert "2025-01-05" in msg
    assert "4" in msg


def test_expiry_warning_not_marked_when_delivery_fails():
    sender = FailingSender(fail_times=99)
    tenant = FakeTenant(expires_at=datetime(2025, 1, 5))
    notifier = Notifier({"telegram": sender})
    day = date(2025, 1, 1)

    first = _run(notifier.send_expiry_warning(tenant, today=day))
    assert first is not None and first.delivered is False
    # A same-day retry is still allowed because the failed send was not marked.
    ledger_has = notifier._ledger.was_sent(  # noqa: SLF001 — asserting behavior
        tenant.tenant_id, NotificationKind.EXPIRY_WARNING, day
    )
    assert ledger_has is False


def test_expiry_warning_none_when_not_in_window():
    sender = RecordingSender()
    tenant = FakeTenant(expires_at=datetime(2025, 2, 1))
    notifier = Notifier({"telegram": sender})
    result = _run(notifier.send_expiry_warning(tenant, today=date(2025, 1, 1)))
    assert result is None
    assert sender.sent == []


# ── daily summary once-per-day (Req 17.4) ──────────────────────────────────────

def _summary():
    now = datetime(2025, 1, 1, 12, 0, 0)
    return DailySummaryData(
        sales_count=5,
        sales_amount=Decimal("2500.00"),
        pending_order_count=3,
        period_start=now - timedelta(hours=24),
        period_end=now,
    )


def test_daily_summary_contains_fields_and_sent_once_per_day():
    sender = RecordingSender()
    tenant = FakeTenant()
    notifier = Notifier({"telegram": sender})
    day = date(2025, 1, 1)

    first = _run(notifier.send_daily_summary(tenant, _summary(), today=day))
    second = _run(notifier.send_daily_summary(tenant, _summary(), today=day))

    assert first is not None and first.delivered is True
    assert second is None  # Req 17.4 once per calendar day
    _, msg = sender.sent[0]
    assert "5" in msg          # sales count
    assert "2500.00" in msg    # sales amount
    assert "3" in msg          # pending orders


# ── compute_daily_summary against a real DB ────────────────────────────────────

@pytest.fixture()
def db():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})

    @event.listens_for(engine, "connect")
    def _fk(conn, _):
        conn.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()
    Base.metadata.drop_all(engine)
    engine.dispose()


def test_compute_daily_summary_counts_last_24h(db):
    now = datetime(2025, 1, 2, 12, 0, 0)
    tenant = Tenant(chat_id="c1")
    db.add(tenant)
    db.commit()
    db.refresh(tenant)

    customer = Customer(tenant_id=tenant.tenant_id, name="A", phone="12345678")
    db.add(customer)
    db.commit()
    db.refresh(customer)

    # Recent pending order (in window) + completed payment (in window)
    recent_order = Order(
        tenant_id=tenant.tenant_id,
        customer_id=customer.customer_id,
        delivery_date=date(2025, 1, 5),
        status="pending",
        created_at=now - timedelta(hours=2),
    )
    db.add(recent_order)
    db.commit()
    db.refresh(recent_order)

    db.add(Payment(
        tenant_id=tenant.tenant_id,
        order_id=recent_order.order_id,
        amount=Decimal("300.00"),
        method="Cash",
        status="completed",
        created_at=now - timedelta(hours=1),
    ))
    # Old payment (outside window) — must be excluded
    old_order = Order(
        tenant_id=tenant.tenant_id,
        customer_id=customer.customer_id,
        delivery_date=date(2025, 1, 5),
        status="pending",
        created_at=now - timedelta(hours=48),
    )
    db.add(old_order)
    db.commit()
    db.refresh(old_order)
    db.add(Payment(
        tenant_id=tenant.tenant_id,
        order_id=old_order.order_id,
        amount=Decimal("999.00"),
        method="Cash",
        status="completed",
        created_at=now - timedelta(hours=48),
    ))
    db.commit()

    summary = compute_daily_summary(db, tenant.tenant_id, now=now)

    assert summary.sales_count == 1
    assert summary.sales_amount == Decimal("300.00")
    assert summary.pending_order_count == 1
