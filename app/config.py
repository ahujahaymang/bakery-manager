"""
Application configuration.

Reads settings from environment variables and .env file.
Supports both SQLite (lightweight, single-tenant) and PostgreSQL (multi-tenant, production).
"""

from pydantic_settings import BaseSettings, SettingsConfigDict
from typing import Literal


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # ── Database ───────────────────────────────────────────────────────────
    DB_ENGINE: Literal["sqlite", "postgresql"] = "sqlite"

    # SQLite (used when DB_ENGINE=sqlite)
    # Directory where per-tenant .db files are stored.
    # Each tenant gets their own file: <SQLITE_DIR>/<tenant_id>.db
    SQLITE_PATH: str = "/data"

    # PostgreSQL / RDS (used when DB_ENGINE=postgresql)
    DB_HOST: str = "localhost"
    DB_PORT: int = 5432
    DB_NAME: str = "ops_db"
    DB_USER: str = "postgres"
    DB_PASSWORD: str = "changeme"

    # ── Telegram ───────────────────────────────────────────────────────────
    TELEGRAM_BOT_TOKEN: str = ""
    # Separate bot token for LOCAL end-to-end testing. Using a distinct token
    # (a second @BotFather bot) avoids the getUpdates 409 conflict that occurs
    # when a local poller competes with the production bot for the same token —
    # and prevents a local instance from intercepting real users' messages.
    # Used only by scripts/run_local_bot.py; production keeps using
    # TELEGRAM_BOT_TOKEN via `python -m app.telegram_listener`.
    TELEGRAM_BOT_DEV_TOKEN: str = ""

    # ── LLM ───────────────────────────────────────────────────────────────
    # OpenAI — used for image processing (GPT-4o vision)
    LLM_API_KEY: str = ""
    LLM_API_BASE_URL: str = "https://api.openai.com/v1"
    LLM_MODEL: str = "gpt-4o-mini"  # vision model for image processing

    # AWS Bedrock — used for agent/intent (Nova Lite)
    BEDROCK_MODEL: str = "amazon.nova-lite-v1:0"
    AWS_REGION: str = "us-east-1"
    # Bedrock uses IAM credentials (AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY env vars)

    # Agent backend: "bedrock" (default) or "gpt" (fallback to OpenAI GPT-4o mini)
    AGENT_BACKEND: str = "bedrock"
    # or instance profile when running on EC2

    # ── S3 backup (SQLite only) ────────────────────────────────────────────
    S3_BACKUP_BUCKET: str = ""        # e.g. "my-ops-bot-backups"
    S3_BACKUP_PREFIX: str = "backups"
    S3_BACKUP_RETAIN_HOURS: int = 24

    # ── Admin ─────────────────────────────────────────────────────────────
    # Your personal Telegram chat_id — skips welcome, operates on owner tenant.
    # Find yours by messaging @userinfobot on Telegram.
    ADMIN_CHAT_ID: str = ""

    # For dedicated single-tenant deployments: set to the owner's chat_id.
    # Admin will operate on that tenant directly without the owner needing
    # to message first. Leave blank for shared multi-tenant deployments.
    OWNER_CHAT_ID: str = ""

    # Instagram DM conversation timeout before order detection
    INSTAGRAM_CONVERSATION_TIMEOUT_MINUTES: int = 30

    # Instagram / Meta
    META_APP_ID: str = ""
    META_APP_SECRET: str = ""
    META_REDIRECT_URI: str = ""
    INSTAGRAM_VERIFY_TOKEN: str = ""
    WEBHOOK_URL: str = ""  # public HTTPS URL of this server

    # ── WhatsApp Cloud API ─────────────────────────────────────────────────
    # Set these once you register a WhatsApp number in your Meta App.
    # Steps: developers.facebook.com → your App → WhatsApp → Getting Started
    WHATSAPP_TOKEN: str = ""               # Permanent access token from Meta App dashboard
    WHATSAPP_PHONE_NUMBER_ID: str = ""     # Phone Number ID (not the actual number)
    WHATSAPP_VERIFY_TOKEN: str = "kitchenos_wa_2026"  # Any secret string you choose

    # ── Auth (app-first pivot) ─────────────────────────────────────────────
    # Device_Session_Token validity. Requirement 2.6 fixes this to a value
    # between 30 and 90 days inclusive; AuthService clamps whatever is
    # configured here into that range before issuing a token.
    OTP_SESSION_DAYS: int = 30

    # ── OTP delivery (tiered: notification channel → SMS) ───────────────────
    # Confirmation windows for tiered OTP delivery (Requirement 3.3, 3.4).
    # If a channel/SMS send is not confirmed within its window, the tier is
    # treated as failed and delivery falls back / fails.
    OTP_CHANNEL_CONFIRMATION_TIMEOUT_SECONDS: int = 30
    OTP_SMS_CONFIRMATION_TIMEOUT_SECONDS: int = 30

    # SMS provider selection — the concrete gateway is swappable via config so
    # no code change is needed to move between providers.
    #   ""      → no SMS provider configured (SMS tier will report delivery failure)
    #   "stub"  → dev-only logging provider (logs the OTP, reports success)
    #   "http"  → generic HTTP/DLT gateway configured by the SMS_* fields below
    #
    # OPERATIONAL DEPENDENCY: In India, transactional OTP SMS requires DLT
    # (Distributed Ledger Technology) registration of the sender ID and the OTP
    # message template with a telecom operator before real delivery works. That
    # registration is an operational task, not a code dependency; SMS_SENDER_ID
    # and SMS_DLT_TEMPLATE_ID below carry the registered values.
    SMS_PROVIDER: str = ""
    SMS_API_BASE_URL: str = ""        # gateway endpoint (for SMS_PROVIDER="http")
    SMS_API_KEY: str = ""             # gateway auth key/token
    SMS_SENDER_ID: str = ""           # DLT-registered sender/header id
    SMS_DLT_TEMPLATE_ID: str = ""     # DLT-registered OTP template id

    # ── WebAuthn / passkeys ────────────────────────────────────────────────
    # Relying Party (RP) identity for the WebAuthn ceremonies. The RP ID must be
    # a registrable domain suffix of the origin the App is served from; the
    # origin is the exact scheme+host(+port) the browser sees. In production the
    # App is served from https://kitchenos.info (see DESIGN.md / nginx layer).
    WEBAUTHN_RP_ID: str = "kitchenos.info"
    WEBAUTHN_RP_NAME: str = "KitchenOS"
    WEBAUTHN_ORIGIN: str = "https://kitchenos.info"

    # ── PWA ────────────────────────────────────────────────────────────────
    # Public URL of the installable web app (PWA). Sent to owners at the end of
    # onboarding so they can log in. Overridable via the APP_URL env var.
    APP_URL: str = "https://kitchenos.info/app"

    # Defense-in-depth HTTP→HTTPS redirect (Req 1.7, 1.8). Enabled by default so
    # production stays safe; set ENABLE_HTTPS_REDIRECT=false for local HTTP dev
    # (otherwise http://localhost is 307-redirected to an https:// URL with no
    # TLS and the browser can't load it).
    ENABLE_HTTPS_REDIRECT: bool = True

    # ── Observability ──────────────────────────────────────────────────────
    # Simple key to protect the /metrics endpoint. Leave empty to disable auth.
    METRICS_KEY: str = ""

    @property
    def DATABASE_URL(self) -> str:
        if self.DB_ENGINE == "sqlite":
            return f"sqlite:///{self.SQLITE_PATH}"
        return (
            f"postgresql+psycopg2://{self.DB_USER}:{self.DB_PASSWORD}"
            f"@{self.DB_HOST}:{self.DB_PORT}/{self.DB_NAME}"
        )


settings = Settings()
