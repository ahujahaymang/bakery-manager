from pydantic_settings import BaseSettings, SettingsConfigDict
from typing import Literal


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # Database
    DB_ENGINE: Literal["sqlite", "postgresql"] = "sqlite"

    # SQLite (used when DB_ENGINE=sqlite)
    SQLITE_PATH: str = "/data/bakery.db"

    # PostgreSQL / RDS (used when DB_ENGINE=postgresql)
    DB_HOST: str = "localhost"
    DB_PORT: int = 5432
    DB_NAME: str = "bakery_ops"
    DB_USER: str = "postgres"
    DB_PASSWORD: str = "changeme"

    # Telegram
    TELEGRAM_BOT_TOKEN: str = ""

    # LLM
    LLM_API_KEY: str = ""
    LLM_API_BASE_URL: str = "https://api.openai.com/v1"
    LLM_MODEL: str = "gpt-4.1-nano"

    # S3 backup (SQLite only)
    S3_BACKUP_BUCKET: str = ""          # e.g. "bakery-ops-backups"
    S3_BACKUP_PREFIX: str = "backups"   # folder inside the bucket
    S3_BACKUP_RETAIN_HOURS: int = 24    # delete backups older than this

    # Admin
    ADMIN_CHAT_ID: str = ""  # your personal Telegram chat_id — skips welcome, uses owner tenant
    # For dedicated per-bakery deployments: set this to the bakery owner's chat_id.
    # Admin will operate directly on that tenant without needing the owner to message first.
    # Leave blank for shared-bot deployments (admin uses /switch to pick a tenant).
    BAKERY_OWNER_CHAT_ID: str = ""

    # App
    WEBHOOK_URL: str = ""

    @property
    def DATABASE_URL(self) -> str:
        if self.DB_ENGINE == "sqlite":
            return f"sqlite:///{self.SQLITE_PATH}"
        return (
            f"postgresql+psycopg2://{self.DB_USER}:{self.DB_PASSWORD}"
            f"@{self.DB_HOST}:{self.DB_PORT}/{self.DB_NAME}"
        )


settings = Settings()
