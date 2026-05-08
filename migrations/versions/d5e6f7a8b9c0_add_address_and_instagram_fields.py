"""add address and instagram fields

Revision ID: d5e6f7a8b9c0
Revises: c4d5e6f7a8b9
Create Date: 2026-05-08

"""
from alembic import op
import sqlalchemy as sa

revision = 'd5e6f7a8b9c0'
down_revision = 'c4d5e6f7a8b9'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('customers', sa.Column('address', sa.String(), nullable=True))
    op.add_column('orders', sa.Column('delivery_address', sa.String(), nullable=True))
    op.add_column('tenants', sa.Column('instagram_account_id', sa.String(), nullable=True))
    op.add_column('tenants', sa.Column('instagram_access_token', sa.String(), nullable=True))
    op.add_column('tenants', sa.Column('messaging_platform', sa.String(), nullable=False, server_default='telegram'))


def downgrade() -> None:
    op.drop_column('tenants', 'messaging_platform')
    op.drop_column('tenants', 'instagram_access_token')
    op.drop_column('tenants', 'instagram_account_id')
    op.drop_column('orders', 'delivery_address')
    op.drop_column('customers', 'address')
