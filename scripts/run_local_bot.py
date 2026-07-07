"""
Local end-to-end runner — Telegram bot (DEV token) + web server + PWA.

Unlike ``scripts/run_local.py`` (web only, no bot), this starts the full stack
so you can test the whole flow locally: message the bot → onboarding / existing
-owner app announcement → open the PWA at /app → OTP sign-in → use the app.

IMPORTANT — always uses the **dev** bot token (``TELEGRAM_BOT_DEV_TOKEN``), never
the production ``TELEGRAM_BOT_TOKEN``. Running a poller on the production token
competes with the live bot (Telegram returns a 409 "terminated by other
getUpdates request" conflict) and a local instance can swallow real users'
messages. Create a second bot with @BotFather and put its token in
``TELEGRAM_BOT_DEV_TOKEN`` in your .env.

Prerequisites:
  - Build the SPA first so app/webapp_static/ exists:
        cd frontend && npm install && npm run build
  - In .env (or as env overrides):
        TELEGRAM_BOT_DEV_TOKEN=<your dev bot token>
        ENABLE_HTTPS_REDIRECT=false          # so http://localhost loads
        APP_URL=http://localhost:8000/app    # link the bot sends points local
        SMS_PROVIDER=stub                    # OTP is logged to this console
        AGENT_BACKEND=gpt                    # use the OpenAI key locally

Run:
    ENABLE_HTTPS_REDIRECT=false APP_URL=http://localhost:8000/app \
    SMS_PROVIDER=stub AGENT_BACKEND=gpt \
    python -m scripts.run_local_bot

Then:
  - Message your DEV bot on Telegram.
  - Open the PWA at http://localhost:8000/app (the link the bot sends).
  - The sign-in OTP is printed to this console (SMS_PROVIDER=stub).
"""

import asyncio
import logging
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("run_local_bot")


def _add_file_logging() -> Path:
    """Tee logs to .kiro_tmp/dev_bot.log so the OTP (SMS stub) is easy to read.

    The ``SMS_PROVIDER=stub`` provider logs the sign-in code via the standard
    logger; mirroring logs to a file means the code can be grepped without
    scrolling the live console.
    """
    log_dir = _ROOT / ".kiro_tmp"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / "dev_bot.log"
    handler = logging.FileHandler(log_path)
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    )
    logging.getLogger().addHandler(handler)
    return log_path


def main() -> None:
    from app.config import settings
    from app.telegram_listener import TelegramBotListener

    log_path = _add_file_logging()
    logger.info("Logging to %s (grep '[SMS STUB]' for sign-in OTPs)", log_path)

    dev_token = settings.TELEGRAM_BOT_DEV_TOKEN
    if not dev_token:
        logger.error(
            "TELEGRAM_BOT_DEV_TOKEN is not set. Create a second bot with @BotFather "
            "and add its token as TELEGRAM_BOT_DEV_TOKEN in .env before running local "
            "end-to-end tests. Refusing to start on the production token."
        )
        sys.exit(1)

    if dev_token == settings.TELEGRAM_BOT_TOKEN:
        logger.error(
            "TELEGRAM_BOT_DEV_TOKEN equals TELEGRAM_BOT_TOKEN — that would conflict "
            "with production. Use a distinct dev bot token."
        )
        sys.exit(1)

    if settings.ENABLE_HTTPS_REDIRECT:
        logger.warning(
            "ENABLE_HTTPS_REDIRECT is on — http://localhost will redirect to https and "
            "fail to load. Re-run with ENABLE_HTTPS_REDIRECT=false for local dev."
        )

    static_dir = _ROOT / "app" / "webapp_static"
    if not (static_dir / "index.html").exists():
        logger.warning(
            "app/webapp_static/index.html not found — build the SPA first: "
            "cd frontend && npm install && npm run build"
        )

    logger.info(
        "Starting local end-to-end stack (DEV bot + web + PWA). "
        "PWA at http://localhost:8000/app"
    )

    # TelegramBotListener starts the FastAPI webhook server (with register_api,
    # so /app and /api/v1 are served) in a background thread, then polls Telegram.
    listener = TelegramBotListener(bot_token=dev_token)
    try:
        asyncio.run(listener.start())
    except KeyboardInterrupt:
        logger.info("Shutting down local bot...")


if __name__ == "__main__":
    main()
