"""
SQLite S3 Backup Service.

Runs as a background task alongside the bot.
Every hour: copies the SQLite file to S3 with a timestamp key.
On startup: prunes backups older than S3_BACKUP_RETAIN_HOURS.

Only active when DB_ENGINE=sqlite and S3_BACKUP_BUCKET is set.
"""

import asyncio
import logging
import shutil
import tempfile
from datetime import datetime, timezone, timedelta
from pathlib import Path

logger = logging.getLogger(__name__)


class BackupService:
    """
    Periodic SQLite → S3 backup.

    Uses a safe copy strategy:
    1. sqlite3 online backup API (via Python sqlite3 module) to get a
       consistent snapshot without locking the live file.
    2. Upload the snapshot to S3.
    3. Delete old backups beyond the retention window.
    """

    def __init__(self, db_path: str, bucket: str, prefix: str, retain_hours: int):
        self.db_path = Path(db_path)
        self.bucket = bucket
        self.prefix = prefix.rstrip("/")
        self.retain_hours = retain_hours
        self._task: asyncio.Task | None = None

    # ── Public API ─────────────────────────────────────────────────────────

    def start(self):
        """Start the background backup loop."""
        if not self.bucket:
            logger.info("S3_BACKUP_BUCKET not set — backups disabled")
            return
        self._task = asyncio.create_task(self._loop())
        logger.info(
            f"Backup service started: {self.db_path} → s3://{self.bucket}/{self.prefix} "
            f"(retain {self.retain_hours}h)"
        )

    async def stop(self):
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    async def backup_now(self) -> str:
        """Run a single backup immediately. Returns the S3 key."""
        return await asyncio.get_event_loop().run_in_executor(None, self._do_backup)

    # ── Internal ───────────────────────────────────────────────────────────

    async def _loop(self):
        """Run backup every hour, prune old backups after each run."""
        while True:
            try:
                key = await self.backup_now()
                logger.info(f"Backup uploaded: s3://{self.bucket}/{key}")
                await self._prune_old_backups()
            except Exception as e:
                logger.error(f"Backup failed: {e}", exc_info=True)
            await asyncio.sleep(3600)  # 1 hour

    def _do_backup(self) -> str:
        """
        Create a consistent SQLite snapshot and upload to S3.
        Uses sqlite3's built-in backup API — safe while the DB is in use.
        """
        import sqlite3
        import boto3

        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        key = f"{self.prefix}/{timestamp}.db"

        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
            tmp_path = tmp.name

        try:
            # Online backup — consistent snapshot without locking
            src = sqlite3.connect(str(self.db_path))
            dst = sqlite3.connect(tmp_path)
            src.backup(dst)
            dst.close()
            src.close()

            # Upload to S3
            s3 = boto3.client("s3")
            s3.upload_file(tmp_path, self.bucket, key)
            return key
        finally:
            Path(tmp_path).unlink(missing_ok=True)

    async def _prune_old_backups(self):
        """Delete S3 objects older than retain_hours."""
        await asyncio.get_event_loop().run_in_executor(None, self._do_prune)

    def _do_prune(self):
        import boto3

        cutoff = datetime.now(timezone.utc) - timedelta(hours=self.retain_hours)
        s3 = boto3.client("s3")

        paginator = s3.get_paginator("list_objects_v2")
        to_delete = []

        for page in paginator.paginate(Bucket=self.bucket, Prefix=self.prefix + "/"):
            for obj in page.get("Contents", []):
                if obj["LastModified"] < cutoff:
                    to_delete.append({"Key": obj["Key"]})

        if to_delete:
            s3.delete_objects(
                Bucket=self.bucket,
                Delete={"Objects": to_delete}
            )
            logger.info(f"Pruned {len(to_delete)} old backup(s) from S3")


def create_backup_service() -> BackupService | None:
    """
    Factory: returns a BackupService if SQLite + S3 are configured, else None.
    """
    from app.config import settings

    if settings.DB_ENGINE != "sqlite":
        return None
    if not settings.S3_BACKUP_BUCKET:
        return None

    return BackupService(
        db_path=settings.SQLITE_PATH,
        bucket=settings.S3_BACKUP_BUCKET,
        prefix=settings.S3_BACKUP_PREFIX,
        retain_hours=settings.S3_BACKUP_RETAIN_HOURS,
    )
