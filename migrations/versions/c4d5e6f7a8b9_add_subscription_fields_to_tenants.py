"""add subscription fields to tenants

Revision ID: c4d5e6f7a8b9
Revises: b3c4d5e6f7a8
Create Date: 2026-05-08

"""
from alembic import op
import sqlalchemy as sa

revision = 'c4d5e6f7a8b9'
down_revision = 'b3c4d5e6f7a8'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('tenants', sa.Column('subscription_status', sa.String(), nullable=False, server_default='pending'))
    op.add_column('tenants', sa.Column('trial_started_at', sa.DateTime(), nullable=True))
    op.add_column('tenants', sa.Column('subscription_expires_at', sa.DateTime(), nullable=True))


def downgrade() -> None:
    op.drop_column('tenants', 'subscription_expires_at')
    op.drop_column('tenants', 'trial_started_at')
    op.drop_column('tenants', 'subscription_status')
