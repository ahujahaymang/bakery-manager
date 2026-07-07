"""
Local dev runner for the App-First PWA — see the app without the Telegram bot.

Serves the FastAPI backend (auth + all /api/v1 domain routers + the booth
routes) and the built PWA at /app, then runs uvicorn on http://localhost:8000.
Unlike the production entrypoint (`python -m app.telegram_listener`) this does
NOT start Telegram polling, so no bot token is needed.

Prerequisites:
  - Build the SPA first so app/webapp_static/ exists:
        cd frontend && npm install && npm run build
  - Disable the HTTPS redirect for local HTTP:
        export ENABLE_HTTPS_REDIRECT=false
    (or add ENABLE_HTTPS_REDIRECT=false to .env)

Run:
    ENABLE_HTTPS_REDIRECT=false python -m scripts.run_local

Then open:  http://localhost:8000/app

Notes:
  - Uses whatever DB_ENGINE/SQLITE_PATH your .env points to. For a throwaway
    local DB, set SQLITE_PATH to a scratch dir and run scripts/seed_owner_users.py
    to create an Owner you can sign in as.
  - OTP delivery: with no channel/SMS provider configured, OTP request returns a
    delivery failure. Set SMS_PROVIDER=stub in .env to have the OTP logged to the
    console for local sign-in testing.
"""

import logging
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("run_local")


def main() -> None:
    import uvicorn
    from app.config import settings
    from app.webhook_server import app, register_api, register_booth

    if settings.ENABLE_HTTPS_REDIRECT:
        logger.warning(
            "ENABLE_HTTPS_REDIRECT is on — http://localhost will redirect to https and "
            "fail to load. Re-run with ENABLE_HTTPS_REDIRECT=false for local dev."
        )

    # Mount the booth routes and the app-first API + SPA. No Telegram bot.
    register_booth()
    register_api()

    static_dir = _ROOT / "app" / "webapp_static"
    if not (static_dir / "index.html").exists():
        logger.warning(
            "app/webapp_static/index.html not found — build the SPA first: "
            "cd frontend && npm install && npm run build"
        )

    logger.info("Serving on http://localhost:8000  (PWA at http://localhost:8000/app)")
    uvicorn.run(app, host="127.0.0.1", port=8000, log_level="info")


if __name__ == "__main__":
    main()
