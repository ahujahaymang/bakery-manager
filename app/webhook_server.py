"""
Webhook server for Instagram (and future WhatsApp/Razorpay) webhooks.

Runs as a FastAPI app alongside the Telegram polling bot.
Requires a public HTTPS URL — use ngrok for local testing or
configure the EC2 instance with nginx + Let's Encrypt for production.

Start alongside the Telegram bot:
    uvicorn app.webhook_server:app --host 0.0.0.0 --port 8000
"""

import logging
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

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
