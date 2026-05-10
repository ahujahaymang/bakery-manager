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
    # OpenAI — used for image processing (GPT-4o vision)
    LLM_API_KEY: str = ""
    LLM_API_BASE_URL: str = "https://api.openai.com/v1"
    LLM_MODEL: str = "gpt-4o-mini"  # vision model for image processing

    # AWS Bedrock — used for agent/intent (Nova Lite)
    BEDROCK_MODEL: str = "amazon.nova-lite-v1:0"
    AWS_REGION: str = "us-east-1"
    # Bedrock uses IAM credentials (AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY env vars)
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
