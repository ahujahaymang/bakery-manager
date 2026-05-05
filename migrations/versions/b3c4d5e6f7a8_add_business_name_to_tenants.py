"""add business_name to tenants

Revision ID: b3c4d5e6f7a8
Revises: a6aa3be8a482
Create Date: 2026-05-04

"""
from alembic import op
import sqlalchemy as sa

revision = 'b3c4d5e6f7a8'
down_revision = 'a6aa3be8a482'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('tenants', sa.Column('business_name', sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column('tenants', 'business_name')
