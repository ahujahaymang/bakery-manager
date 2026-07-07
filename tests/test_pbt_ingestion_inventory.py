"""
Property-based tests for the ``inventory`` ingestion doc-type.

These extend design **Property 32** ("confirm routes to the matching domain
create; a domain failure persists no data") to the ``inventory`` document type,
which routes a confirmed draft through ``InventoryService.create_item`` per
stock row (Req 15.6, 15.10).

They mirror the fixture/setup pattern of ``tests/test_ingestion_inventory.py``:
invoke ``ingestion_router.confirm`` directly with a canned ``AuthedUser`` and an
in-memory SQLite session (business tables registered on ``Base``; a ``Tenant``
row seeded; the ``TENANT_ID`` / ``OWNER`` pattern).

Because hypothesis re-runs the test body many times within a single test
function, a *function-scoped* fixture would not reset between examples. Each
example therefore builds its own fresh in-memory engine + seeded session and
drops the schema afterwards, keeping examples fully independent.

Generators are constrained to the ranges ``InventoryService`` accepts (mirroring
``tests/test_inventory_service.py``): positive 2-dp quantities/costs within
``Numeric(10, 2)``, units drawn from the service's ``VALID_UNITS``, and
alphanumeric names that are unique per batch (case-insensitively) so a valid
batch never trips the service's duplicate-name guard.
"""

import string
from decimal import Decimal
from uuid import uuid4

import pytest
from hypothesis import given, settings, strategies as st
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

# Register all model tables on the shared Base.
import app.models  # noqa: F401
from app.database import Base
from app.models import InventoryItem, Tenant

from app.api import ingestion_router
from app.api.deps import AuthedUser
from app.api.errors import APIError
from app.api.ingestion_router import IngestionConfirmRequest
from app.services.inventory_service import VALID_UNITS


TENANT_ID = uuid4()
OWNER = AuthedUser(uuid4(), TENANT_ID, "owner", uuid4())

TWO_PLACES = Decimal("0.01")
MONEY = dict(min_value=Decimal("0.01"), max_value=Decimal("999999.99"), places=2)


# ── Per-example in-memory registry (fresh + isolated) ────────────────────────

def _fresh_session():
    """Build a fresh in-memory engine with a seeded tenant for one example."""
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(engine, "connect")
    def _fk(conn, _):  # noqa: ANN001
        conn.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine)
    session = SessionLocal()
    session.add(Tenant(tenant_id=TENANT_ID, chat_id="pbt_inventory_ingest"))
    session.commit()
    return engine, session


def _confirm(db, draft):
    return ingestion_router.confirm(
        IngestionConfirmRequest(doc_type="inventory", draft=draft), OWNER, db
    )


def _q(value) -> Decimal:
    """Quantize to 2 dp for exact comparison irrespective of storage scale."""
    return Decimal(str(value)).quantize(TWO_PLACES)


# ── Strategies ───────────────────────────────────────────────────────────────

# Alphanumeric names (no surrounding whitespace) so the persisted name — which
# the service stores stripped/as-is — round-trips exactly.
_name = st.text(alphabet=string.ascii_letters + string.digits, min_size=1, max_size=20)
_unit = st.sampled_from(sorted(VALID_UNITS))
# Blank category exercises the "defaults to ingredient" branch; otherwise a
# valid category the service accepts.
_category = st.sampled_from(["", "ingredient", "packaging"])
_money = st.decimals(**MONEY)


@st.composite
def valid_items(draw):
    """A 1..8 length list of valid inventory rows with unique names."""
    n = draw(st.integers(min_value=1, max_value=8))
    names = draw(
        st.lists(_name, min_size=n, max_size=n, unique_by=lambda s: s.lower())
    )
    return [
        {
            "name": name,
            "category": draw(_category),
            "quantity": draw(_money),
            "unit": draw(_unit),
            "cost": draw(_money),
        }
        for name in names
    ]


# An invalid row: blank name, missing/None quantity or cost, or a non-numeric
# quantity — each of which the confirm pre-validation rejects.
def _invalid_row(good_name):
    return st.one_of(
        st.just({"name": "", "quantity": Decimal("1.00"), "unit": "kg", "cost": Decimal("1.00")}),
        st.just({"name": good_name, "quantity": None, "unit": "kg", "cost": Decimal("1.00")}),
        st.just({"name": good_name, "quantity": Decimal("1.00"), "unit": "kg", "cost": None}),
        st.just({"name": good_name, "quantity": "not-a-number", "unit": "kg", "cost": Decimal("1.00")}),
    )


@st.composite
def items_with_invalid(draw):
    """A list (1..8 rows) that contains at least one invalid row."""
    valids = draw(valid_items())
    bad = draw(_invalid_row("BadRow"))
    # Insert the bad row at a random position, trimming to keep length <= 8.
    valids = valids[:7]
    pos = draw(st.integers(min_value=0, max_value=len(valids)))
    valids.insert(pos, bad)
    return valids


# ── Property A — round-trip: confirm persists exactly the submitted rows ──────

# Feature: app-first-pivot, Property 32: inventory confirm persists each submitted
# stock row exactly (name/quantity/unit/cost_per_unit round-trip; blank category
# defaults to "ingredient"). Validates Req 15.6.
@settings(max_examples=100, deadline=None)
@given(items=valid_items())
def test_property_a_confirm_inventory_round_trip(items):
    engine, db = _fresh_session()
    try:
        body = _confirm(db, {"items": items})

        # Exactly len(items) rows created for the tenant.
        assert len(body["items"]) == len(items)
        stored = (
            db.query(InventoryItem)
            .filter(InventoryItem.tenant_id == TENANT_ID)
            .all()
        )
        assert len(stored) == len(items)

        by_name = {i.name: i for i in stored}
        for submitted in items:
            persisted = by_name[submitted["name"]]
            assert persisted.name == submitted["name"]
            assert persisted.unit == submitted["unit"]
            assert _q(persisted.quantity) == _q(submitted["quantity"])
            assert _q(persisted.cost_per_unit) == _q(submitted["cost"])
            expected_category = (submitted["category"].strip().lower() or "ingredient")
            assert persisted.category == expected_category
    finally:
        db.close()
        Base.metadata.drop_all(engine)
        engine.dispose()


# ── Property B — all-or-nothing: an invalid row persists nothing ──────────────

# Feature: app-first-pivot, Property 32: an inventory confirm containing any
# invalid row is rejected with a validation error and persists no rows.
# Validates Req 15.10.
@settings(max_examples=100, deadline=None)
@given(items=items_with_invalid())
def test_property_b_confirm_inventory_all_or_nothing(items):
    engine, db = _fresh_session()
    try:
        with pytest.raises(APIError):
            _confirm(db, {"items": items})
        # Nothing from this confirm is persisted (pre-validation rejects the
        # whole batch before any create).
        assert (
            db.query(InventoryItem)
            .filter(InventoryItem.tenant_id == TENANT_ID)
            .count()
            == 0
        )
    finally:
        db.close()
        Base.metadata.drop_all(engine)
        engine.dispose()
