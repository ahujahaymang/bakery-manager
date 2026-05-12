"""add customization fields to order_items

Revision ID: f7a8b9c0d1e2
Revises: e6f7a8b9c0d1
Create Date: 2026-05-12

"""
from alembic import op
import sqlalchemy as sa

revision = 'f7a8b9c0d1e2'
down_revision = 'e6f7a8b9c0d1'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('order_items',
        sa.Column('customization_charge', sa.Numeric(10, 2), nullable=False, server_default='0'))
    op.add_column('order_items',
        sa.Column('customization_note', sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column('order_items', 'customization_note')
    op.drop_column('order_items', 'customization_charge')
