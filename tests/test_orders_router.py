"""
Unit/integration tests for the Orders router (app-first pivot, task 8.1).

Exercises ``app/api/orders_router.py`` end-to-end through a FastAPI TestClient
with the auth dependencies overridden so the router runs against the in-memory
tenant DB from ``conftest`` (Req 9.1-9.9):

- create → status pending; required-field and value-range validation
- list with delivery-date / status filters
- deliver (pending→delivered) and its transition guard
- cancel (pending→cancelled, record retained) and its transition guard
- Owner-only delete (Staff rejected with 403)
"""

from datetime import date, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api import deps
from app.api.errors import register_error_handlers
from app.api.deps import AuthedUser
from app.api.orders_router import router
from app.models import Base, Order
from app.services.customer_service import CustomerService


@pytest.fixture(scope="function")
def db():
    """
    In-memory SQLite shared across threads (TestClient runs sync endpoints in a
    worker thread), so the router and the test see the same database. Overrides
    the conftest ``db`` fixture for this module.
    """
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(engine, "connect")
    def _fk(conn, _):
        conn.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()
    Base.metadata.drop_all(engine)
    engine.dispose()


def _tomorrow() -> date:
    return date.today() + timedelta(days=1)


def _owner(tenant_id) -> AuthedUser:
    return AuthedUser(user_id=uuid4(), tenant_id=tenant_id, role="owner", device_id=uuid4())


def _staff(tenant_id) -> AuthedUser:
    return AuthedUser(user_id=uuid4(), tenant_id=tenant_id, role="staff", device_id=uuid4())


def _client(db, user) -> TestClient:
    app = FastAPI()
    register_error_handlers(app)
    app.include_router(router)
    # Only override the identity + tenant-session choke points; require_owner
    # runs its real logic against the overridden get_current_user.
    app.dependency_overrides[deps.get_current_user] = lambda: user
    app.dependency_overrides[deps.get_tenant_db_for_user] = lambda: db
    return TestClient(app, raise_server_exceptions=True)


def _make_customer(db, tenant_id, name="Alice", phone="9876543210"):
    return CustomerService(db).create_customer(tenant_id, name, phone)


def _order_body(phone="9876543210", qty=2, price="150", delivery_date=None):
    return {
        "customer_identifier": phone,
        "delivery_date": (delivery_date or _tomorrow()).isoformat(),
        "items": [{"recipe_name": "Brownie", "quantity": qty, "selling_price": price}],
    }


# ── create (Req 9.1, 9.7, 9.8) ────────────────────────────────────────────────

def test_create_order_is_pending(db, tenant_id):
    _make_customer(db, tenant_id)
    client = _client(db, _owner(tenant_id))

    resp = client.post("/api/v1/orders", json=_order_body())

    assert resp.status_code == 201
    body = resp.json()
    assert body["status"] == "pending"
    assert len(body["items"]) == 1


def test_create_order_missing_customer_is_validation_error(db, tenant_id):
    client = _client(db, _owner(tenant_id))

    resp = client.post("/api/v1/orders", json=_order_body(phone="Nobody"))

    assert resp.status_code == 400
    assert resp.json()["error"] == "validation_error"


def test_create_order_quantity_above_max_rejected(db, tenant_id):
    _make_customer(db, tenant_id)
    client = _client(db, _owner(tenant_id))

    resp = client.post("/api/v1/orders", json=_order_body(qty=1_000_000))

    assert resp.status_code == 400
    body = resp.json()
    assert body["error"] == "validation_error"
    assert body["field"] == "quantity"
    # No order persisted.
    assert db.query(Order).count() == 0


def test_create_order_price_above_max_rejected(db, tenant_id):
    _make_customer(db, tenant_id)
    client = _client(db, _owner(tenant_id))

    resp = client.post("/api/v1/orders", json=_order_body(price="10000000.00"))

    assert resp.status_code == 400
    assert resp.json()["field"] == "selling_price"
    assert db.query(Order).count() == 0


# ── list + filters (Req 9.2, 9.5) ─────────────────────────────────────────────

def test_list_filters_by_status(db, tenant_id):
    _make_customer(db, tenant_id)
    client = _client(db, _owner(tenant_id))
    o1 = client.post("/api/v1/orders", json=_order_body()).json()
    client.post("/api/v1/orders", json=_order_body())
    # Deliver one so statuses differ.
    client.post(f"/api/v1/orders/{o1['order_id']}/deliver")

    pending = client.get("/api/v1/orders", params={"status": "pending"}).json()
    delivered = client.get("/api/v1/orders", params={"status": "delivered"}).json()

    assert len(pending) == 1
    assert all(o["status"] == "pending" for o in pending)
    assert len(delivered) == 1
    assert delivered[0]["order_id"] == o1["order_id"]


def test_list_filters_by_delivery_date(db, tenant_id):
    _make_customer(db, tenant_id)
    client = _client(db, _owner(tenant_id))
    d1 = _tomorrow()
    d2 = d1 + timedelta(days=5)
    client.post("/api/v1/orders", json=_order_body(delivery_date=d1))
    client.post("/api/v1/orders", json=_order_body(delivery_date=d2))

    matches = client.get("/api/v1/orders", params={"delivery_date": d2.isoformat()}).json()

    assert len(matches) == 1
    assert matches[0]["delivery_date"] == d2.isoformat()


def test_list_invalid_status_rejected(db, tenant_id):
    client = _client(db, _owner(tenant_id))
    resp = client.get("/api/v1/orders", params={"status": "shipped"})
    assert resp.status_code == 400
    assert resp.json()["field"] == "status"


# ── deliver / cancel + transition guard (Req 9.3, 9.4, 9.9) ───────────────────

def test_deliver_pending_order(db, tenant_id):
    _make_customer(db, tenant_id)
    client = _client(db, _owner(tenant_id))
    order = client.post("/api/v1/orders", json=_order_body()).json()

    resp = client.post(f"/api/v1/orders/{order['order_id']}/deliver")

    assert resp.status_code == 200
    assert resp.json()["status"] == "delivered"


def test_cancel_pending_order_retains_record(db, tenant_id):
    _make_customer(db, tenant_id)
    client = _client(db, _owner(tenant_id))
    order = client.post("/api/v1/orders", json=_order_body()).json()

    resp = client.post(f"/api/v1/orders/{order['order_id']}/cancel")

    assert resp.status_code == 200
    assert resp.json()["status"] == "cancelled"
    # Record retained (Req 9.4).
    assert db.query(Order).filter(Order.order_id == UUID_of(order)).count() == 1


def test_cancel_delivered_order_is_invalid_transition(db, tenant_id):
    _make_customer(db, tenant_id)
    client = _client(db, _owner(tenant_id))
    order = client.post("/api/v1/orders", json=_order_body()).json()
    client.post(f"/api/v1/orders/{order['order_id']}/deliver")

    resp = client.post(f"/api/v1/orders/{order['order_id']}/cancel")

    assert resp.status_code == 409
    assert resp.json()["error"] == "invalid_transition"
    # Status unchanged.
    row = db.query(Order).filter(Order.order_id == UUID_of(order)).first()
    assert row.status == "delivered"


def test_deliver_cancelled_order_is_invalid_transition(db, tenant_id):
    _make_customer(db, tenant_id)
    client = _client(db, _owner(tenant_id))
    order = client.post("/api/v1/orders", json=_order_body()).json()
    client.post(f"/api/v1/orders/{order['order_id']}/cancel")

    resp = client.post(f"/api/v1/orders/{order['order_id']}/deliver")

    assert resp.status_code == 409
    assert resp.json()["error"] == "invalid_transition"


def test_deliver_missing_order_is_404(db, tenant_id):
    client = _client(db, _owner(tenant_id))
    resp = client.post(f"/api/v1/orders/{uuid4()}/deliver")
    assert resp.status_code == 404


# ── delete (Req 9.6) ──────────────────────────────────────────────────────────

def test_owner_can_delete_order(db, tenant_id):
    _make_customer(db, tenant_id)
    client = _client(db, _owner(tenant_id))
    order = client.post("/api/v1/orders", json=_order_body()).json()

    resp = client.request("DELETE", f"/api/v1/orders/{order['order_id']}")

    assert resp.status_code == 204
    assert db.query(Order).filter(Order.order_id == UUID_of(order)).count() == 0


def test_staff_cannot_delete_order(db, tenant_id):
    _make_customer(db, tenant_id)
    owner_client = _client(db, _owner(tenant_id))
    order = owner_client.post("/api/v1/orders", json=_order_body()).json()

    staff_client = _client(db, _staff(tenant_id))
    resp = staff_client.request("DELETE", f"/api/v1/orders/{order['order_id']}")

    assert resp.status_code == 403
    # Order retained.
    assert db.query(Order).filter(Order.order_id == UUID_of(order)).count() == 1


def UUID_of(order_json):
    from uuid import UUID
    return UUID(order_json["order_id"])
