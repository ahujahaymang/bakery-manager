"""
Unit tests for the Insights chat route (``POST /api/v1/insights/chat``).

These exercise ``app/api/insights_router.py::chat`` **without a live LLM**: the
shared ``AgentService.run`` is monkeypatched to a stub that records what it was
called with and returns a canned string. Kept dependency-light like the other
router tests — the auth dependencies are overridden and the route runs against
an in-memory tenant DB.

Covers:
- happy path returns ``{"answer": ...}`` (agent output passed straight through)
- tenant scoping: the ToolExecutor is built with the token-derived tenant_id,
  and ``tenant_id`` is forwarded to the agent (Req 16.1)
- conversation history is passed through to the agent
- available to Staff (full tools for all roles on the chat path)
- special marker strings (INVOICE_PDF: / CHOOSE:) are returned verbatim
- an agent failure maps to a clean 502 (no stack trace)
"""

from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.api.insights_router as insights_router
from app.api import deps
from app.api.deps import AuthedUser
from app.api.errors import register_error_handlers
from app.api.insights_router import router
from app.models import Base, Tenant


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture(scope="function")
def db():
    """In-memory SQLite shared across threads (TestClient uses a worker thread)."""
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


@pytest.fixture(scope="function")
def tenant_id(db):
    t = Tenant(chat_id="insights_chat_route_001")
    db.add(t)
    db.commit()
    db.refresh(t)
    return t.tenant_id


def _owner(tenant_id) -> AuthedUser:
    return AuthedUser(user_id=uuid4(), tenant_id=tenant_id, role="owner", device_id=uuid4())


def _staff(tenant_id) -> AuthedUser:
    return AuthedUser(user_id=uuid4(), tenant_id=tenant_id, role="staff", device_id=uuid4())


def _client(db, user) -> TestClient:
    app = FastAPI()
    register_error_handlers(app)
    app.include_router(router)
    app.dependency_overrides[deps.get_current_user] = lambda: user
    app.dependency_overrides[deps.get_tenant_db_for_user] = lambda: db
    return TestClient(app, raise_server_exceptions=True)


class _StubAgent:
    """
    Records the arguments of the last ``run`` call and returns a canned answer.

    Installed by monkeypatching ``AgentService.run``/``close`` so no real LLM
    client is ever constructed with a live backend for these tests.
    """

    calls: list = []

    @classmethod
    def install(cls, monkeypatch, answer="Your revenue last week was ₹4,200."):
        cls.calls = []

        def _init(self, llm_client=None):
            # No real LLM client — keep the test free of network / API keys.
            self.llm_client = None

        async def _run(self, user_message, history, tool_executor, tenant_id="unknown"):
            cls.calls.append(
                {
                    "user_message": user_message,
                    "history": history,
                    "tool_executor": tool_executor,
                    "tenant_id": tenant_id,
                }
            )
            return answer

        async def _close(self):
            return None

        monkeypatch.setattr(insights_router.AgentService, "__init__", _init)
        monkeypatch.setattr(insights_router.AgentService, "run", _run)
        monkeypatch.setattr(insights_router.AgentService, "close", _close)


# ── Happy path + response shape ────────────────────────────────────────────────

def test_chat_returns_answer(db, tenant_id, monkeypatch):
    _StubAgent.install(monkeypatch, answer="Hello from the agent.")
    client = _client(db, _owner(tenant_id))

    resp = client.post("/api/v1/insights/chat", json={"message": "How are sales?"})

    assert resp.status_code == 200
    assert resp.json() == {"answer": "Hello from the agent."}
    assert _StubAgent.calls[0]["user_message"] == "How are sales?"


# ── Tenant scoping (Req 16.1) ──────────────────────────────────────────────────

def test_chat_is_tenant_scoped(db, tenant_id, monkeypatch):
    """
    The ToolExecutor is constructed with the token-derived tenant_id, and that
    tenant_id is forwarded to the agent — never taken from request input.
    """
    _StubAgent.install(monkeypatch)

    built = {}
    real_init = insights_router.ToolExecutor.__init__

    def _spy_init(self, db_arg, tid):
        built["db"] = db_arg
        built["tenant_id"] = tid
        real_init(self, db_arg, tid)

    monkeypatch.setattr(insights_router.ToolExecutor, "__init__", _spy_init)

    client = _client(db, _owner(tenant_id))
    resp = client.post("/api/v1/insights/chat", json={"message": "revenue?"})

    assert resp.status_code == 200
    # ToolExecutor built with the authenticated user's tenant + tenant DB.
    assert built["tenant_id"] == tenant_id
    assert built["db"] is db
    # And the agent received the same tenant id (as a string).
    assert _StubAgent.calls[0]["tenant_id"] == str(tenant_id)


# ── History pass-through ────────────────────────────────────────────────────────

def test_chat_passes_history_through(db, tenant_id, monkeypatch):
    _StubAgent.install(monkeypatch)
    client = _client(db, _owner(tenant_id))

    history = [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "hello"},
    ]
    resp = client.post(
        "/api/v1/insights/chat",
        json={"message": "and now?", "history": history},
    )

    assert resp.status_code == 200
    assert _StubAgent.calls[0]["history"] == history


def test_chat_defaults_history_to_empty(db, tenant_id, monkeypatch):
    _StubAgent.install(monkeypatch)
    client = _client(db, _owner(tenant_id))

    resp = client.post("/api/v1/insights/chat", json={"message": "first message"})

    assert resp.status_code == 200
    assert _StubAgent.calls[0]["history"] == []


# ── Available to all roles (full tools on the chat path) ───────────────────────

def test_chat_available_to_staff(db, tenant_id, monkeypatch):
    _StubAgent.install(monkeypatch, answer="Staff can chat too.")
    client = _client(db, _staff(tenant_id))

    resp = client.post("/api/v1/insights/chat", json={"message": "what is my profit?"})

    assert resp.status_code == 200
    assert resp.json()["answer"] == "Staff can chat too."


# ── Special marker strings pass through verbatim ────────────────────────────────

def test_chat_returns_marker_string_verbatim(db, tenant_id, monkeypatch):
    marker = "CHOOSE: option A | option B"
    _StubAgent.install(monkeypatch, answer=marker)
    client = _client(db, _owner(tenant_id))

    resp = client.post("/api/v1/insights/chat", json={"message": "which one?"})

    assert resp.status_code == 200
    assert resp.json()["answer"] == marker


# ── Agent failure → clean 502 ───────────────────────────────────────────────────

def test_chat_agent_failure_maps_to_502(db, tenant_id, monkeypatch):
    def _init(self, llm_client=None):
        self.llm_client = None

    async def _boom(self, user_message, history, tool_executor, tenant_id="unknown"):
        raise RuntimeError("LLM backend unreachable")

    async def _close(self):
        return None

    monkeypatch.setattr(insights_router.AgentService, "__init__", _init)
    monkeypatch.setattr(insights_router.AgentService, "run", _boom)
    monkeypatch.setattr(insights_router.AgentService, "close", _close)

    client = _client(db, _owner(tenant_id))
    resp = client.post("/api/v1/insights/chat", json={"message": "boom"})

    assert resp.status_code == 502
    body = resp.json()
    assert body["error"] == "agent_unavailable"
    # No stack trace / internal detail leaked.
    assert "LLM backend unreachable" not in str(body)


# ── Validation: empty message rejected ──────────────────────────────────────────

def test_chat_empty_message_rejected(db, tenant_id, monkeypatch):
    _StubAgent.install(monkeypatch)
    client = _client(db, _owner(tenant_id))

    resp = client.post("/api/v1/insights/chat", json={"message": ""})

    assert resp.status_code == 422
