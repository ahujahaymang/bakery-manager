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

    # ── LLM ───────────────────────────────────────────────────────────────
    LLM_API_KEY: str = ""
    LLM_API_BASE_URL: str = "https://api.openai.com/v1"
    LLM_MODEL: str = "gpt-4.1-nano"

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

    # App
    WEBHOOK_URL: str = ""

    # Instagram / Meta
    META_APP_ID: str = ""
    META_APP_SECRET: str = ""
    META_REDIRECT_URI: str = ""       # e.g. https://yourdomain.com/instagram/callback
    INSTAGRAM_VERIFY_TOKEN: str = ""  # any secret string you choose

    @property
    def DATABASE_URL(self) -> str:
        if self.DB_ENGINE == "sqlite":
            return f"sqlite:///{self.SQLITE_PATH}"
        return (
            f"postgresql+psycopg2://{self.DB_USER}:{self.DB_PASSWORD}"
            f"@{self.DB_HOST}:{self.DB_PORT}/{self.DB_NAME}"
        )


settings = Settings()
