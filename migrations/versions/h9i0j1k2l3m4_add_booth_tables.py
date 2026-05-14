"""Add booth_sessions, booth_session_items; update orders, tenants, payments

Revision ID: h9i0j1k2l3m4
Revises: g8h9i0j1k2l3
Create Date: 2026-05-13 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = 'h9i0j1k2l3m4'
down_revision: Union[str, Sequence[str], None] = 'g8h9i0j1k2l3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── New tables ─────────────────────────────────────────────────────────

    op.create_table(
        'booth_sessions',
        sa.Column('session_id',  sa.String(36), nullable=False),
        sa.Column('tenant_id',   sa.String(36), nullable=False),
        sa.Column('name',        sa.String(),   nullable=False),
        sa.Column('started_at',  sa.DateTime(), nullable=False),
        sa.Column('ended_at',    sa.DateTime(), nullable=True),
        sa.Column('created_at',  sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['tenant_id'], ['tenants.tenant_id']),
        sa.PrimaryKeyConstraint('session_id'),
    )
    op.create_index('ix_booth_sessions_tenant_id', 'booth_sessions', ['tenant_id'])

    op.create_table(
        'booth_session_items',
        sa.Column('item_id',     sa.String(36),          nullable=False),
        sa.Column('session_id',  sa.String(36),          nullable=False),
        sa.Column('variant_id',  sa.String(36),          nullable=False),
        sa.Column('booth_price', sa.Numeric(10, 2),      nullable=False),
        sa.Column('stock_qty',   sa.Integer(),            nullable=True),
        sa.Column('sold_qty',    sa.Integer(),            nullable=False, server_default='0'),
        sa.Column('created_at',  sa.DateTime(),           nullable=False),
        sa.ForeignKeyConstraint(['session_id'], ['booth_sessions.session_id']),
        sa.ForeignKeyConstraint(['variant_id'], ['product_variants.variant_id']),
        sa.PrimaryKeyConstraint('item_id'),
    )
    op.create_index('ix_booth_session_items_session_id', 'booth_session_items', ['session_id'])

    # ── New columns on existing tables ─────────────────────────────────────

    # orders.booth_session_id — NULL for regular orders, set for booth orders
    op.add_column('orders', sa.Column(
        'booth_session_id', sa.String(36), nullable=True
    ))
    op.create_index('ix_orders_booth_session_id', 'orders', ['booth_session_id'])

    # tenants.razorpay_key_id / razorpay_key_secret
    op.add_column('tenants', sa.Column('razorpay_key_id',     sa.String(), nullable=True))
    op.add_column('tenants', sa.Column('razorpay_key_secret', sa.String(), nullable=True))

    # payments.razorpay_payment_id / status
    op.add_column('payments', sa.Column('razorpay_payment_id', sa.String(), nullable=True))
    op.add_column('payments', sa.Column(
        'status', sa.String(), nullable=False, server_default='completed'
    ))
    op.create_index('ix_payments_razorpay_payment_id', 'payments', ['razorpay_payment_id'])


def downgrade() -> None:
    op.drop_index('ix_payments_razorpay_payment_id', table_name='payments')
    op.drop_column('payments', 'status')
    op.drop_column('payments', 'razorpay_payment_id')

    op.drop_column('tenants', 'razorpay_key_secret')
    op.drop_column('tenants', 'razorpay_key_id')

    op.drop_index('ix_orders_booth_session_id', table_name='orders')
    op.drop_column('orders', 'booth_session_id')

    op.drop_index('ix_booth_session_items_session_id', table_name='booth_session_items')
    op.drop_table('booth_session_items')

    op.drop_index('ix_booth_sessions_tenant_id', table_name='booth_sessions')
    op.drop_table('booth_sessions')
