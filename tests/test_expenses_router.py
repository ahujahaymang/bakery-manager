"""
Unit tests for the expenses domain router (task 13.1).

These exercise ``app/api/expenses_router.py`` as a thin Owner-only HTTP adapter
over the ``PurchaseExpense`` service-layer path:

- ``POST /api/v1/expenses`` — create with amount/category/description validation
  and capital-asset flag (Req 14.1, 14.2, 14.3, 14.6)
- ``GET  /api/v1/expenses`` — list with category + inclusive date-range filter,
  rejecting inverted ranges (Req 14.4, 14.5, 14.6)

The environment has no HTTP test client (httpx) installed, so these tests invoke
the router's route callables directly with a canned principal and an in-memory
SQLite session. Role gating (require_owner) is covered structurally by
``test_api_deps.py``; here we assert the Owner path wiring, validation, and the
date-range filter behavior.
"""

from datetime import date
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

# Register all model tables on the shared Base.
import app.models  # noqa: F401
from app.database import Base
from app.models import PurchaseExpense, Tenant
from app.api import expenses_router
from app.api.deps import AuthedUser, require_owner
from app.api.errors import ForbiddenError, ValidationError as APIValidationError
from app.api.schemas import ExpenseCreateRequest

from pydantic import ValidationError as PydanticValidationError


TENANT_ID = uuid4()
OWNER = AuthedUser(uuid4(), TENANT_ID, "owner", uuid4())
STAFF = AuthedUser(uuid4(), TENANT_ID, "staff", uuid4())


@pytest.fixture()
def db_session():
    engine = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}
    )

    @event.listens_for(engine, "connect")
    def _fk(conn, _):
        conn.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine)
    session = SessionLocal()
    session.add(Tenant(tenant_id=TENANT_ID, chat_id="test_chat_expenses"))
    session.commit()
    yield session
    session.close()
    Base.metadata.drop_all(engine)
    engine.dispose()


def _create(db, user, **overrides):
    payload = dict(
        amount=Decimal("100.00"),
        expense_date=date(2024, 1, 15),
        category="ingredients",
        is_capital=False,
    )
    payload.update(overrides)
    return expenses_router.create_expense(ExpenseCreateRequest(**payload), user, db)


# ── create (Req 14.1, 14.2, 14.3) ──────────────────────────────────────────────

def test_create_expense_persists_and_serializes(db_session):
    body = _create(db_session, OWNER, description="flour 10kg")

    assert body["amount"] == Decimal("100.00")
    assert body["category"] == "ingredients"
    assert body["description"] == "flour 10kg"
    assert "expense_id" in body
    assert db_session.query(PurchaseExpense).count() == 1


def test_create_expense_preserves_capital_flag(db_session):
    body = _create(db_session, OWNER, category="equipment", is_capital=True)

    # Model stores the flag as the SQLite-safe string "true".
    assert body["is_capital"] == "true"
    row = db_session.query(PurchaseExpense).one()
    assert row.is_capital == "true"


def test_create_expense_non_capital_flag_false(db_session):
    _create(db_session, OWNER, is_capital=False)
    row = db_session.query(PurchaseExpense).one()
    assert row.is_capital == "false"


def test_create_expense_unrecognized_category_rejected_no_record(db_session):
    with pytest.raises(APIValidationError) as exc:
        _create(db_session, OWNER, category="bribes")
    assert exc.value.status_code == 400
    assert exc.value.field == "category"
    # No record created (Req 14.2).
    assert db_session.query(PurchaseExpense).count() == 0


def test_create_expense_amount_below_min_rejected_by_request_model(db_session):
    with pytest.raises(PydanticValidationError):
        ExpenseCreateRequest(
            amount=Decimal("0.00"),
            expense_date=date(2024, 1, 1),
            category="ingredients",
        )


def test_create_expense_amount_above_max_rejected_by_request_model(db_session):
    with pytest.raises(PydanticValidationError):
        ExpenseCreateRequest(
            amount=Decimal("1000000000.00"),
            expense_date=date(2024, 1, 1),
            category="ingredients",
        )


def test_create_expense_amount_at_upper_bound_accepted(db_session):
    body = _create(db_session, OWNER, amount=Decimal("999999999.99"))
    assert body["amount"] == Decimal("999999999.99")


def test_create_expense_description_over_500_chars_rejected(db_session):
    with pytest.raises(PydanticValidationError):
        ExpenseCreateRequest(
            amount=Decimal("10.00"),
            expense_date=date(2024, 1, 1),
            category="ingredients",
            description="x" * 501,
        )


# ── list filter (Req 14.4, 14.5) ───────────────────────────────────────────────

def _seed(db, category, day):
    db.add(
        PurchaseExpense(
            tenant_id=TENANT_ID,
            amount=Decimal("10.00"),
            expense_date=date(2024, 1, day),
            category=category,
            is_capital="false",
        )
    )
    db.commit()


def test_list_returns_only_matching_category_and_in_range(db_session):
    _seed(db_session, "ingredients", 5)
    _seed(db_session, "ingredients", 15)
    _seed(db_session, "ingredients", 25)
    _seed(db_session, "rent", 15)  # wrong category

    rows = expenses_router.list_expenses(
        OWNER,
        db_session,
        category="ingredients",
        start_date=date(2024, 1, 10),
        end_date=date(2024, 1, 20),
    )

    assert len(rows) == 1
    assert rows[0]["category"] == "ingredients"
    assert rows[0]["expense_date"] == date(2024, 1, 15)


def test_list_range_is_inclusive_of_endpoints(db_session):
    _seed(db_session, "rent", 10)
    _seed(db_session, "rent", 20)

    rows = expenses_router.list_expenses(
        OWNER,
        db_session,
        start_date=date(2024, 1, 10),
        end_date=date(2024, 1, 20),
    )
    assert {r["expense_date"] for r in rows} == {date(2024, 1, 10), date(2024, 1, 20)}


def test_list_no_filters_returns_all(db_session):
    _seed(db_session, "rent", 10)
    _seed(db_session, "ingredients", 20)
    rows = expenses_router.list_expenses(OWNER, db_session)
    assert len(rows) == 2


def test_list_inverted_range_rejected(db_session):
    _seed(db_session, "rent", 10)
    with pytest.raises(APIValidationError) as exc:
        expenses_router.list_expenses(
            OWNER,
            db_session,
            start_date=date(2024, 1, 20),
            end_date=date(2024, 1, 10),
        )
    assert exc.value.status_code == 400
    assert exc.value.field == "date"


# ── Owner-only gate (Req 14.6) ─────────────────────────────────────────────────

def test_staff_rejected_by_owner_gate():
    with pytest.raises(ForbiddenError) as exc:
        require_owner(STAFF)
    assert exc.value.status_code == 403
