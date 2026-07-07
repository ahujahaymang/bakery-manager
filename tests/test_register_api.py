"""
Tests for ``register_api()`` in ``app/webhook_server.py`` (task 4.5).

``register_api()`` mutates the module-global FastAPI ``app`` (mounts routes,
extends CORS, adds middleware), so each test reloads ``app.webhook_server`` to
get a fresh, isolated ``app`` instance. Assertions inspect the app object
(routes / middleware stack) rather than issuing HTTP requests — issuing an HTTP
request would be redirected by the HTTPSRedirectMiddleware and is unnecessary to
verify wiring.

Covered:
- auth router mounted at /api/v1/auth (Req 1 / API foundation)
- production origin added to CORS allow_origins (Req 20.2)
- HTTPSRedirectMiddleware present as defense-in-depth (Req 1.7, 1.8)
- /app static SPA mounted when webapp_static/ exists (html=True fallback)
- /app mount guarded (no crash, not mounted) when webapp_static/ is absent
"""

import importlib
from pathlib import Path

import pytest
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.httpsredirect import HTTPSRedirectMiddleware


@pytest.fixture()
def fresh_server():
    """Reload webhook_server so each test gets a pristine module-global app."""
    import app.webhook_server as ws
    ws = importlib.reload(ws)
    yield ws
    # Reload once more so we don't leak this test's mutated app to other tests.
    importlib.reload(ws)


def _api_paths(ws):
    # Starlette >=1.x wraps included routers lazily (routes are not flattened
    # into app.routes), so enumerate registered paths via the OpenAPI schema.
    return set(ws.app.openapi().get("paths", {}))


def _mount_names(ws):
    return {getattr(r, "name", None) for r in ws.app.routes}


def test_register_api_mounts_auth_router(fresh_server):
    ws = fresh_server
    ws.register_api()
    paths = _api_paths(ws)
    # auth_router uses prefix="/api/v1/auth"
    assert "/api/v1/auth/otp/request" in paths
    assert "/api/v1/auth/otp/verify" in paths


def test_register_api_mounts_all_domain_routers(fresh_server):
    ws = fresh_server
    ws.register_api()
    paths = _api_paths(ws)
    # Task 18.1: every domain router is mounted under its own /api/v1 prefix
    # alongside the auth router, completing the additive mount (Req 20.2, 20.4).
    for prefix in (
        "/api/v1/sell",
        "/api/v1/orders",
        "/api/v1/inventory",
        "/api/v1/recipes",
        "/api/v1/customers",
        "/api/v1/invoices",
        "/api/v1/expenses",
        "/api/v1/insights",
        "/api/v1/ingestion",
    ):
        assert any(p.startswith(prefix) for p in paths), f"missing routes for {prefix}"


def test_register_api_extends_cors_to_production_origin(fresh_server):
    ws = fresh_server
    ws.register_api()
    cors = next(mw for mw in ws.app.user_middleware if mw.cls is CORSMiddleware)
    assert ws.settings.WEBAUTHN_ORIGIN in cors.kwargs["allow_origins"]


def test_register_api_adds_https_redirect_middleware(fresh_server):
    ws = fresh_server
    ws.register_api()
    assert any(
        mw.cls is HTTPSRedirectMiddleware for mw in ws.app.user_middleware
    )


def test_register_api_mounts_spa_when_bundle_exists(fresh_server, tmp_path, monkeypatch):
    ws = fresh_server
    # Point webhook_server's __file__ dir at a temp tree containing webapp_static/.
    static_dir = tmp_path / "webapp_static"
    static_dir.mkdir()
    (static_dir / "index.html").write_text("<!doctype html><title>app</title>")
    monkeypatch.setattr(ws, "__file__", str(tmp_path / "webhook_server.py"))

    ws.register_api()
    assert "webapp" in _mount_names(ws)


def test_register_api_guards_missing_spa_bundle(fresh_server, tmp_path, monkeypatch):
    ws = fresh_server
    # Temp dir with NO webapp_static/ — mount must be skipped, no exception.
    monkeypatch.setattr(ws, "__file__", str(tmp_path / "webhook_server.py"))

    ws.register_api()  # must not raise
    assert "webapp" not in _mount_names(ws)
    assert not (tmp_path / "webapp_static").exists()
