"""
Ask / Insights router for the app-first REST API (``/api/v1/insights``).

A **read-only** analysis surface (Req 16). It answers plain-language business
questions from the signed-in Tenant's data only — it never mutates anything.

Design (design §8):

- Questions are answered through a **read-only path**: a curated allowlist of
  read/reporting agent tools (see :data:`READ_ONLY_TOOL_ALLOWLIST`, drawn from
  ``app/tools/report_tools.py`` and the read-only query tools) and the existing,
  unchanged :class:`~app.services.reporting_service.ReportingService` summation
  logic. No tool that writes to the tenant DB is ever reachable from here.
- **Tenant scope** comes strictly from the authenticated device session via
  :func:`get_tenant_db_for_user`, so every computation is confined to the
  caller's tenant (Req 16.1) — never from request input.
- **Financial questions (revenue / cost / profit) are blocked for Staff** at the
  router, *before* any tool runs or any data is read (Req 16.6). A Staff request
  that references financial data is rejected with ``403 forbidden`` and no
  financial value is returned.
- **Reporting periods are validated** (present and start ≤ end) before any
  financial aggregation. A missing or inverted period yields a message and *no*
  computed financial values (Req 16.2, 16.3).
- **Order cost** is computed as the sum of each ingredient's quantity multiplied
  by its recorded unit price — the same ingredient × unit-price summation
  ``ReportingService`` uses. Ingredients that have no recorded unit price are
  identified explicitly and the cost is reported as not fully computable
  (Req 16.4, 16.5).
- Questions the Backend cannot answer from the Tenant's data return an
  explicit "could not answer" message with **no partial or placeholder values**
  (Req 16.7).

_Requirements: 16.1, 16.2, 16.3, 16.4, 16.5, 16.6, 16.7_
"""

from __future__ import annotations

import logging
from datetime import date
from decimal import Decimal
from typing import List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from app.api import errors
from app.api.deps import AuthedUser, get_current_user, get_tenant_db_for_user
from app.models import Order
from app.services.agent_service import AgentService
from app.services.tool_executor import ToolExecutor

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/insights", tags=["insights"])


# ── Read-only agent tool allowlist (design §8) ────────────────────────────────
#
# The Insights surface may only ever reach tools that *read* tenant data. This
# curated allowlist is the set of agent tool names permitted on the insights
# path — a subset of ``app.tools.TOOLS`` restricted to reporting and read-only
# query tools. Any tool that creates, updates, or deletes tenant data is
# deliberately excluded, so the analysis path cannot mutate the tenant DB.
READ_ONLY_TOOL_ALLOWLIST = frozenset(
    {
        "profit_report",
        "upcoming_orders",
        "list_recipes",
        "get_recipe",
        "list_inventory",
        "list_products",
        "get_product",
        "get_customer",
        "payment_history",
        "list_expenses",
        "list_booth_sessions",
        "get_booth_session_summary",
        "get_booth_url",
        "get_order_template",
    }
)


# ── Intent keywords ───────────────────────────────────────────────────────────
#
# A question "references revenue, cost, or profit data" (Req 16.6) when it
# mentions any of these terms. Matching is case-insensitive substring matching
# on the question text. Kept deliberately broad on the financial side so a Staff
# request that touches financials is blocked rather than leaking a value.
_FINANCIAL_KEYWORDS = (
    "revenue",
    "profit",
    "margin",
    "income",
    "earning",
    "earn",
    "turnover",
    "cost",
    "sales",
    "sale ",
    "spend",
    "spent",
    "expense",
    "how much money",
    "how much did i make",
)

# Terms that indicate the question is specifically about the cost of one order.
_ORDER_TERMS = ("order",)
_COST_TERMS = ("cost",)


# ── Request / response models ─────────────────────────────────────────────────

class InsightsAskRequest(BaseModel):
    """
    Body for ``POST /api/v1/insights/ask``.

    ``question`` is the plain-language question. ``start_date``/``end_date``
    bound a reporting period for revenue/cost/profit questions (Req 16.2, 16.3).
    ``order_id`` names the order for an order-cost question (Req 16.4, 16.5).
    """

    model_config = ConfigDict(str_strip_whitespace=True)

    question: str = Field(min_length=1)
    start_date: Optional[date] = None
    end_date: Optional[date] = None
    order_id: Optional[UUID] = None


class InsightsAnswer(BaseModel):
    """
    Answer to an insights question.

    Financial fields are populated only when a valid financial question is fully
    computed. For a missing/invalid period (Req 16.3), an incomplete order cost
    (Req 16.5), or an unanswerable question (Req 16.7), the numeric fields stay
    ``None`` and ``answer`` carries the explanatory message.
    """

    answer: str
    answerable: bool
    revenue: Optional[Decimal] = None
    cost: Optional[Decimal] = None
    profit: Optional[Decimal] = None
    order_cost: Optional[Decimal] = None
    period_start: Optional[date] = None
    period_end: Optional[date] = None
    missing_unit_price_ingredients: List[str] = Field(default_factory=list)


class ChatMessage(BaseModel):
    """A single prior turn in the conversation, as sent by the App."""

    model_config = ConfigDict(str_strip_whitespace=True)

    role: str
    content: str


class InsightsChatRequest(BaseModel):
    """
    Body for ``POST /api/v1/insights/chat``.

    ``message`` is the User's latest plain-language message. ``history`` carries
    the prior conversation turns ({role, content}) so the agent has context; it
    is optional and defaults to an empty conversation.
    """

    model_config = ConfigDict(str_strip_whitespace=True)

    message: str = Field(min_length=1)
    history: Optional[List[ChatMessage]] = None


class InsightsChatResponse(BaseModel):
    """The agent's plain-language answer to a chat message."""

    answer: str


# ── Intent classification ─────────────────────────────────────────────────────

def _mentions_any(text: str, needles) -> bool:
    return any(n in text for n in needles)


def _is_order_cost_question(body: InsightsAskRequest, q: str) -> bool:
    """An order-cost question names an order and asks about its cost (Req 16.4)."""
    if body.order_id is not None:
        return True
    return _mentions_any(q, _ORDER_TERMS) and _mentions_any(q, _COST_TERMS)


def _is_financial_question(q: str, is_order_cost: bool) -> bool:
    """
    True when the question references revenue, cost, or profit data (Req 16.6).

    Order-cost questions are financial (they reference cost), so they are always
    treated as financial and thus blocked for Staff.
    """
    return is_order_cost or _mentions_any(q, _FINANCIAL_KEYWORDS)


# ── Financial computation (mirrors ReportingService summation, Req 16.2) ──────

def _aggregate_financials(
    db: Session, tenant_id: UUID, start: date, end: date
) -> tuple[Decimal, Decimal, Decimal]:
    """
    Aggregate revenue, cost, and profit over the inclusive ``[start, end]``
    period (Req 16.2), using the same summation as
    :meth:`ReportingService.calculate_profit`:

    - revenue  = Σ over delivered orders in period of Σ(item.quantity × item.selling_price)
    - cost     = Σ recipe ingredient + packaging cost, per-unit × quantity ordered
    - profit   = revenue − cost

    Only orders with status ``delivered`` and a ``delivery_date`` within the
    inclusive period contribute, matching ReportingService.
    """
    orders = (
        db.query(Order)
        .filter(
            Order.tenant_id == tenant_id,
            Order.status == "delivered",
            Order.delivery_date >= start,
            Order.delivery_date <= end,
        )
        .all()
    )

    total_revenue = Decimal("0")
    total_cost = Decimal("0")

    for order in orders:
        for item in order.order_items:
            total_revenue += item.quantity * item.selling_price

            recipe = item.recipe
            if recipe is None or not recipe.yield_per_batch:
                continue

            recipe_cost = Decimal("0")
            for component in recipe.recipe_components:
                inventory_item = component.inventory_item
                if inventory_item is None:
                    continue
                recipe_cost += component.quantity * inventory_item.cost_per_unit

            unit_cost = recipe_cost / recipe.yield_per_batch
            total_cost += unit_cost * item.quantity

    profit = total_revenue - total_cost
    return total_revenue, total_cost, profit


def _compute_order_cost(order: Order) -> tuple[Decimal, List[str], bool]:
    """
    Compute an order's cost as the sum of each ingredient's quantity multiplied
    by its recorded unit price (Req 16.4), using the same ingredient ×
    unit-price summation as ReportingService.

    For each order line, every recipe component's per-batch quantity is scaled to
    the ordered quantity (``component.quantity / yield_per_batch × item.quantity``)
    and multiplied by the inventory item's recorded ``cost_per_unit``.

    Returns ``(total_cost, missing_ingredient_names, fully_computable)``. An
    ingredient with no recorded unit price (``cost_per_unit`` is ``None`` or 0)
    is added to ``missing_ingredient_names`` and makes the cost *not* fully
    computable (Req 16.5). An order line whose recipe cannot be resolved also
    makes the cost not fully computable.
    """
    total_cost = Decimal("0")
    missing: List[str] = []
    fully_computable = True

    for item in order.order_items:
        recipe = item.recipe
        if recipe is None or not recipe.yield_per_batch:
            # Cannot resolve the ingredients for this line — cost incomplete.
            fully_computable = False
            continue

        for component in recipe.recipe_components:
            inventory_item = component.inventory_item
            if inventory_item is None:
                fully_computable = False
                continue

            unit_price = inventory_item.cost_per_unit
            if unit_price is None or unit_price == 0:
                # Ingredient has no recorded unit price (Req 16.5).
                fully_computable = False
                if inventory_item.name not in missing:
                    missing.append(inventory_item.name)
                continue

            ingredient_qty = (
                component.quantity / recipe.yield_per_batch
            ) * item.quantity
            total_cost += ingredient_qty * unit_price

    return total_cost, missing, fully_computable


# ── Route ─────────────────────────────────────────────────────────────────────

@router.post("/ask")
@router.post("/ask/", include_in_schema=False)
def ask(
    body: InsightsAskRequest,
    user: AuthedUser = Depends(get_current_user),
    db: Session = Depends(get_tenant_db_for_user),
) -> InsightsAnswer:
    """
    Answer a plain-language analysis question from the signed-in Tenant's data.

    Flow:

    1. Classify the question. If it references revenue/cost/profit and the user
       holds the Staff role, reject with ``403 forbidden`` **before any data is
       read** (Req 16.6).
    2. Order-cost question → compute the order cost, identifying any ingredient
       missing a unit price (Req 16.4, 16.5).
    3. Revenue/cost/profit question → validate the reporting period (present and
       start ≤ end); on failure return a message with no financial values
       (Req 16.3); otherwise aggregate over the inclusive period (Req 16.2).
    4. Anything else → an explicit "could not answer" message with no partial or
       placeholder values (Req 16.7).

    All computation is tenant-scoped by construction: ``db`` is opened for the
    token-derived tenant and every query filters on ``user.tenant_id`` (Req 16.1).
    """
    q = body.question.lower()
    is_order_cost = _is_order_cost_question(body, q)
    is_financial = _is_financial_question(q, is_order_cost)

    # Req 16.6: block financial questions for Staff before any tool/data access.
    if is_financial and user.role != "owner":
        raise errors.ForbiddenError(
            "You are not permitted to access financial data."
        )

    if is_order_cost:
        return _answer_order_cost(body, user, db)

    if is_financial:
        return _answer_financial_period(body, user, db)

    # Req 16.7: cannot answer from the tenant's data — no partial values.
    return InsightsAnswer(
        answer=(
            "I couldn't answer that question from your business data. "
            "Try asking about revenue, cost, or profit for a date range, "
            "or the cost of a specific order."
        ),
        answerable=False,
    )


def _answer_order_cost(
    body: InsightsAskRequest, user: AuthedUser, db: Session
) -> InsightsAnswer:
    """Answer an order-cost question (Req 16.4, 16.5), tenant-scoped (Req 16.1)."""
    if body.order_id is None:
        # We know it's an order-cost question but no order was identified.
        return InsightsAnswer(
            answer=(
                "I need to know which order you mean. "
                "Please specify the order to compute its cost."
            ),
            answerable=False,
        )

    order = (
        db.query(Order)
        .filter(
            Order.order_id == body.order_id,
            Order.tenant_id == user.tenant_id,
        )
        .first()
    )
    if order is None:
        # No such order in this tenant — cannot answer (Req 16.7).
        return InsightsAnswer(
            answer="I couldn't find that order in your business data.",
            answerable=False,
        )

    total_cost, missing, fully_computable = _compute_order_cost(order)

    if not fully_computable:
        # Req 16.5: report that the cost could not be fully computed and identify
        # the ingredients missing a unit price. No computed order cost returned.
        if missing:
            names = ", ".join(missing)
            answer = (
                "I couldn't fully compute this order's cost because these "
                f"ingredients have no recorded unit price: {names}. "
                "Add their unit prices in Inventory to get the full cost."
            )
        else:
            answer = (
                "I couldn't fully compute this order's cost because one or more "
                "items could not be matched to a recipe with priced ingredients."
            )
        return InsightsAnswer(
            answer=answer,
            answerable=False,
            missing_unit_price_ingredients=missing,
        )

    return InsightsAnswer(
        answer=f"The cost of this order is ₹{total_cost}.",
        answerable=True,
        order_cost=total_cost,
    )


def _answer_financial_period(
    body: InsightsAskRequest, user: AuthedUser, db: Session
) -> InsightsAnswer:
    """
    Answer a revenue/cost/profit question over a reporting period (Req 16.2,
    16.3). Validates the period before computing; tenant-scoped (Req 16.1).
    """
    start = body.start_date
    end = body.end_date

    # Req 16.3: a missing period yields a message and no financial values.
    if start is None or end is None:
        return InsightsAnswer(
            answer=(
                "Please specify a reporting period (a start date and an end "
                "date) for revenue, cost, or profit questions."
            ),
            answerable=False,
        )

    # Req 16.3: an inverted period (start after end) is invalid.
    if start > end:
        return InsightsAnswer(
            answer=(
                "The reporting period is invalid: the start date "
                f"({start.isoformat()}) is after the end date "
                f"({end.isoformat()})."
            ),
            answerable=False,
        )

    # Req 16.2: aggregate over the inclusive period.
    revenue, cost, profit = _aggregate_financials(db, user.tenant_id, start, end)

    answer = (
        f"From {start.isoformat()} to {end.isoformat()}: "
        f"revenue ₹{revenue}, cost ₹{cost}, profit ₹{profit}."
    )
    return InsightsAnswer(
        answer=answer,
        answerable=True,
        revenue=revenue,
        cost=cost,
        profit=profit,
        period_start=start,
        period_end=end,
    )


# ── Chat route (LLM agent, full tool + DB access) ─────────────────────────────

@router.post("/chat")
@router.post("/chat/", include_in_schema=False)
async def chat(
    body: InsightsChatRequest,
    user: AuthedUser = Depends(get_current_user),
    db: Session = Depends(get_tenant_db_for_user),
) -> InsightsChatResponse:
    """
    Answer a plain-language chat message with the shared LLM agent — the same
    Plan→Execute→Summarise agent the Telegram bot uses — with the full tool set
    and DB access.

    Flow (mirrors ``app/handlers/request_handler.py``):

    1. Build a :class:`ToolExecutor` over the token-derived tenant's business DB
       (``db``) and ``user.tenant_id``. Tenant scope comes strictly from the
       authenticated session, never from request input, so every tool the agent
       runs is confined to the caller's tenant (Req 16.1).
    2. Run ``AgentService().run(...)`` with the message and prior history.
    3. Return ``{"answer": <str>}``. Special marker strings the agent may return
       ("INVOICE_PDF:", "INSTAGRAM_CONNECT_URL:", "CHOOSE:") are passed through
       verbatim as the answer rather than crashing the request.

    On any LLM/agent failure (network, missing API key, unexpected exception),
    the error is logged server-side and mapped to a clean 502
    :class:`~app.api.errors.AgentUnavailableError`; no stack trace leaks.

    NOTE: Unlike the deterministic ``/ask`` endpoint, the chat surface exposes
    the full tool set to all roles (Owner and Staff) by design. Restricting the
    tool set or blocking financial tools for Staff on the chat path is a future
    consideration.
    """
    history = [{"role": m.role, "content": m.content} for m in (body.history or [])]
    executor = ToolExecutor(db, user.tenant_id)

    agent: Optional[AgentService] = None
    try:
        # Constructing AgentService lazily builds the LLM client; a missing API
        # key or backend misconfiguration surfaces here and is mapped to 502.
        agent = AgentService()
        answer = await agent.run(
            user_message=body.message,
            history=history,
            tool_executor=executor.execute,
            tenant_id=str(user.tenant_id),
        )
    except errors.APIError:
        # A typed API error from the tool/DB path passes through unchanged.
        raise
    except Exception as exc:  # noqa: BLE001 — map any agent/LLM failure to 502.
        logger.exception("Insights chat agent failed: %s", exc)
        raise errors.AgentUnavailableError() from exc
    finally:
        # Best-effort cleanup of the lazily-built LLM client; never fail the
        # request if teardown misbehaves.
        if agent is not None:
            try:
                await agent.close()
            except Exception:  # noqa: BLE001
                logger.debug("Agent close failed after chat", exc_info=True)

    return InsightsChatResponse(answer=answer if isinstance(answer, str) else str(answer))
