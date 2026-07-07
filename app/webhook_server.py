"""
Webhook server for Instagram (and future WhatsApp/Razorpay) webhooks.

Runs as a FastAPI app alongside the Telegram polling bot.
Requires a public HTTPS URL — use ngrok for local testing or
configure the EC2 instance with nginx + Let's Encrypt for production.

Start alongside the Telegram bot:
    uvicorn app.webhook_server:app --host 0.0.0.0 --port 8000
"""

import hashlib
import hmac
import logging

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings

logger = logging.getLogger(__name__)

app = FastAPI(
    title="KitchenOS Webhook Server",
    description="Receives webhooks from Instagram, WhatsApp, and payment providers",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://kitchenos.info", "http://localhost:8000"],
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type"],
)


async def _verify_meta_signature(request: Request) -> bytes:
    """
    Verify the X-Hub-Signature-256 header sent by Meta on every webhook POST.

    Raises HTTP 403 if the signature is missing or invalid.
    Returns the raw request body so callers don't need to read it again.
    """
    body = await request.body()

    # Skip verification if the app secret is not configured (dev/test mode)
    if not settings.META_APP_SECRET:
        logger.warning("META_APP_SECRET not set — skipping webhook signature verification")
        return body

    signature_header = request.headers.get("X-Hub-Signature-256", "")
    if not signature_header.startswith("sha256="):
        logger.warning("Missing or malformed X-Hub-Signature-256 header")
        raise HTTPException(status_code=403, detail="Missing webhook signature")

    expected = "sha256=" + hmac.new(
        settings.META_APP_SECRET.encode(),
        body,
        hashlib.sha256,
    ).hexdigest()

    if not hmac.compare_digest(signature_header, expected):
        logger.warning("Webhook signature mismatch — possible spoofed request")
        raise HTTPException(status_code=403, detail="Invalid webhook signature")

    return body


@app.get("/health")
async def health():
    """Deep health check: verifies the registry DB is readable."""
    try:
        from app.database import open_registry_db
        from app.models import Tenant
        with open_registry_db() as db:
            db.query(Tenant).limit(1).all()
        return {"status": "ok"}
    except Exception as e:
        logger.error(f"Health check failed: {e}")
        return {"status": "degraded", "detail": str(e)}


@app.get("/metrics")
async def get_metrics(hours: int = 24, key: str = ""):
    """
    Developer observability endpoint.

    Returns LLM cost/usage, active users, errors, latency and top tools
    for the last N hours (default 24).

    Protected by a simple key check (set METRICS_KEY in .env).
    Returns 403 if key is wrong, 200 with empty-looking stats if key not configured.
    """
    from app.config import settings
    metrics_key = getattr(settings, "METRICS_KEY", "")
    if metrics_key and key != metrics_key:
        raise HTTPException(status_code=403, detail="Invalid metrics key")

    from app.services.metrics_service import metrics
    return metrics.summary(window_hours=max(1, min(hours, 168)))


def register_whatsapp(handler, admin_notifier=None):
    """
    Create and register the WhatsApp listener and its webhook routes.

    Called from telegram_listener._start_webhook_server() at startup.
    The WhatsAppListener is created here so telegram_listener stays
    platform-agnostic — it only passes the shared RequestHandler.

    No-op if WHATSAPP_TOKEN is not configured.
    """
    from app.whatsapp_listener import WhatsAppListener, create_whatsapp_router
    wa = WhatsAppListener(handler=handler, admin_notifier=admin_notifier)
    if not wa.enabled:
        logger.info("WhatsApp not configured — set WHATSAPP_TOKEN and WHATSAPP_PHONE_NUMBER_ID to enable")
        return
    router = create_whatsapp_router(wa)
    app.include_router(router)
    logger.info("WhatsApp webhook routes registered at /whatsapp/webhook")


def register_instagram(instagram_listener):
    """
    Register Instagram webhook routes.
    Called from telegram_listener after the InstagramListener is created.
    """
    from app.instagram_listener import create_instagram_router
    router = create_instagram_router(instagram_listener)
    app.include_router(router)
    logger.info("Instagram webhook routes registered at /instagram/webhook")


def register_booth():
    """
    Register booth routes and static files.
    Called once at startup from telegram_listener.
    """
    from pathlib import Path
    from fastapi.staticfiles import StaticFiles
    from app.booth.router import router as booth_router, booth_alias
    from app.booth.razorpay_router import router as razorpay_router

    # Serve register static files (CSS, JS)
    static_dir = Path(__file__).parent / "booth" / "static"
    app.mount("/register/static", StaticFiles(directory=str(static_dir)), name="register-static")
    app.mount("/booth/static", StaticFiles(directory=str(static_dir)), name="booth-static-alias")

    app.include_router(booth_router)
    app.include_router(booth_alias)
    app.include_router(razorpay_router)
    logger.info("Register routes registered at /register/{tenant_id}")


def _extend_cors_origins(origin: str) -> None:
    """
    Add ``origin`` to the existing CORSMiddleware ``allow_origins`` if missing.

    register_api() runs at startup *before* the ASGI server builds the
    middleware stack, so the middleware options can still be mutated safely.
    Idempotent: re-adding an origin that is already allowed is a no-op.
    """
    if not origin:
        return
    for mw in app.user_middleware:
        if mw.cls is CORSMiddleware:
            origins = mw.kwargs.setdefault("allow_origins", [])
            if origin not in origins:
                origins.append(origin)
            return


def register_api():
    """
    Register the app-first REST API and the built PWA static bundle.

    Called once at startup from telegram_listener alongside register_booth().

    Scope (tasks 4.5 + 18.1):
      - mount the auth router (``/api/v1/auth``) and every domain router
        (sell, orders, inventory, recipes, customers, invoices, expenses,
        insights, ingestion) — each declares its own ``/api/v1/...`` prefix
        (Req 20.2, 20.4)
      - mount the built PWA at ``/app`` from ``app/webapp_static/`` with SPA
        fallback (``html=True``), mirroring the booth static mount
      - extend CORS to the production origin (Req 20.2)
      - add HTTPSRedirectMiddleware as defense-in-depth (Req 1.7, 1.8)

    This is an additive mount: the Telegram poller, Instagram/WhatsApp webhooks
    and the booth router are untouched, so disabling ``register_api()`` reverts
    the system to chat+booth behavior.
    """
    from pathlib import Path
    from fastapi.staticfiles import StaticFiles
    from starlette.middleware.httpsredirect import HTTPSRedirectMiddleware
    from app.api.errors import register_error_handlers
    from app.api import (
        auth_router,
        sell_router,
        orders_router,
        inventory_router,
        recipes_router,
        customers_router,
        invoices_router,
        expenses_router,
        insights_router,
        ingestion_router,
    )

    # Mount the auth router alongside every domain router. Each router already
    # declares its own ``prefix="/api/v1/..."`` so no prefix is passed here.
    for domain_router in (
        auth_router,
        sell_router,
        orders_router,
        inventory_router,
        recipes_router,
        customers_router,
        invoices_router,
        expenses_router,
        insights_router,
        ingestion_router,
    ):
        app.include_router(domain_router.router)

    # Register the shared API exception handlers so typed APIError (and any
    # uncaught service-layer ValueError) render as the designed JSON bodies
    # (401/403/400/409/404/422/429/502) instead of a bare 500.
    register_error_handlers(app)

    # Allow the production origin the App is served from (defaults to
    # https://kitchenos.info) so the PWA can call the Backend over HTTP(S)
    # (Req 20.2).
    _extend_cors_origins(settings.WEBAUTHN_ORIGIN)

    # Defense-in-depth HTTPS enforcement (Req 1.7, 1.8). HTTP→HTTPS redirect is
    # normally handled by the nginx layer; this middleware is a fallback. In
    # production uvicorn honours X-Forwarded-Proto from nginx, so already-HTTPS
    # requests proxied over HTTP are not redirected. Skippable for local HTTP
    # dev via ENABLE_HTTPS_REDIRECT=false (defaults on so production is safe).
    if settings.ENABLE_HTTPS_REDIRECT:
        app.add_middleware(HTTPSRedirectMiddleware)
    else:
        logger.warning("HTTPSRedirectMiddleware disabled (ENABLE_HTTPS_REDIRECT=false) — local dev only")

    # Serve the built PWA with SPA fallback to index.html. The bundle may not
    # exist yet (frontend not built), so guard the mount to avoid crashing
    # startup before the SPA has been built.
    static_dir = Path(__file__).parent / "webapp_static"
    if static_dir.is_dir():
        app.mount(
            "/app",
            StaticFiles(directory=str(static_dir), html=True),
            name="webapp",
        )
        logger.info("App PWA mounted at /app from %s", static_dir)
    else:
        logger.warning(
            "webapp_static/ not found at %s — /app not mounted "
            "(build the SPA to enable it)",
            static_dir,
        )

    logger.info(
        "App API registered under /api/v1 (auth, sell, orders, inventory, "
        "recipes, customers, invoices, expenses, insights, ingestion)"
    )
