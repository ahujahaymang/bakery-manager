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
    allow_origins=["*"],
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
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
    return {"status": "ok"}


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
