"""
Conversation Service — persisted chat history.

Replaces the in-memory _history dict in RequestHandler.
History is stored in each tenant's business DB so it survives
server restarts and is queryable for debugging.
"""

import logging
from typing import Dict, List
from uuid import UUID
from datetime import datetime

import sqlalchemy as sa
from sqlalchemy.orm import Session

from app.models import ConversationMessage

logger = logging.getLogger(__name__)

# How many most-recent messages to load per (tenant, chat) pair
MAX_HISTORY = 8  # 8 turns = enough context, not enough to carry stale patterns


class ConversationService:
    """
    Read and write conversation history for a single tenant's DB session.

    Each call to append() writes immediately so nothing is lost on crash.
    load() fetches the last MAX_HISTORY rows ordered by created_at.
    """

    def __init__(self, db: Session, tenant_id: UUID):
        self.db = db
        self.tenant_id = tenant_id

    # ── Public API ─────────────────────────────────────────────────────────

    def load(self, chat_id: str) -> List[Dict]:
        """
        Return the last MAX_HISTORY messages for this (tenant, chat) as a
        list of {role, content} dicts — the format the LLM expects.
        """
        rows = (
            self.db.query(ConversationMessage)
            .filter(
                ConversationMessage.tenant_id == self.tenant_id,
                ConversationMessage.chat_id == chat_id,
            )
            .order_by(ConversationMessage.created_at.asc())
            .all()
        )
        # Keep only the last MAX_HISTORY entries
        rows = rows[-MAX_HISTORY:]
        return [{"role": r.role, "content": r.content} for r in rows]

    def append(self, chat_id: str, role: str, content: str) -> None:
        """
        Persist a single message and prune old rows so the table doesn't
        grow unboundedly (keep the most recent MAX_HISTORY * 2 rows per chat).
        """
        msg = ConversationMessage(
            tenant_id=self.tenant_id,
            chat_id=chat_id,
            role=role,
            content=content,
            created_at=datetime.utcnow(),
        )
        self.db.add(msg)
        try:
            self.db.commit()
        except Exception as e:
            self.db.rollback()
            logger.warning(f"Failed to persist conversation message: {e}")
            return

        # Prune: keep only the newest MAX_HISTORY * 2 rows per (tenant, chat)
        # This runs occasionally — cheap enough to do inline
        self._prune(chat_id)

    def clear(self, chat_id: str) -> None:
        """Delete all history for a (tenant, chat) pair — used on /switch."""
        self.db.query(ConversationMessage).filter(
            ConversationMessage.tenant_id == self.tenant_id,
            ConversationMessage.chat_id == chat_id,
        ).delete()
        self.db.commit()

    # ── Internal ───────────────────────────────────────────────────────────

    def _prune(self, chat_id: str) -> None:
        """
        Delete rows older than the newest MAX_HISTORY * 2 for this chat.
        Runs after every append — SQLite makes this fast with the index.
        """
        keep = MAX_HISTORY * 2
        # Find the created_at cutoff: the (keep+1)-th newest row
        subq = (
            self.db.query(ConversationMessage.created_at)
            .filter(
                ConversationMessage.tenant_id == self.tenant_id,
                ConversationMessage.chat_id == chat_id,
            )
            .order_by(ConversationMessage.created_at.desc())
            .offset(keep)
            .limit(1)
            .scalar()
        )
        if subq is None:
            return  # fewer than keep rows — nothing to prune

        try:
            self.db.query(ConversationMessage).filter(
                ConversationMessage.tenant_id == self.tenant_id,
                ConversationMessage.chat_id == chat_id,
                ConversationMessage.created_at <= subq,
            ).delete()
            self.db.commit()
        except Exception as e:
            self.db.rollback()
            logger.warning(f"Failed to prune conversation history: {e}")
