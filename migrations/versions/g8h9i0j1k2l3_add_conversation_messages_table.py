"""Add conversation_messages table for persisted chat history

Revision ID: g8h9i0j1k2l3
Revises: f7a8b9c0d1e2
Create Date: 2026-05-13 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'g8h9i0j1k2l3'
down_revision: Union[str, Sequence[str], None] = 'f7a8b9c0d1e2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'conversation_messages',
        sa.Column('message_id', sa.String(36), nullable=False),
        sa.Column('tenant_id', sa.String(36), nullable=False),
        sa.Column('chat_id', sa.String(), nullable=False),
        sa.Column('role', sa.String(), nullable=False),
        sa.Column('content', sa.Text(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['tenant_id'], ['tenants.tenant_id']),
        sa.PrimaryKeyConstraint('message_id'),
    )
    op.create_index('ix_conv_msg_tenant_chat', 'conversation_messages',
                    ['tenant_id', 'chat_id'], unique=False)


def downgrade() -> None:
    op.drop_index('ix_conv_msg_tenant_chat', table_name='conversation_messages')
    op.drop_table('conversation_messages')
