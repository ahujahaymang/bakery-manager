"""Initial migration with all tables and indexes

Revision ID: 07c9fd7f518a
Revises: 
Create Date: 2026-04-13 15:13:43.420268

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '07c9fd7f518a'
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # Create tenants table
    op.create_table(
        'tenants',
        sa.Column('tenant_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('chat_id', sa.String(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint('tenant_id'),
        sa.UniqueConstraint('chat_id')
    )
    op.create_index(op.f('ix_tenants_chat_id'), 'tenants', ['chat_id'], unique=True)
    
    # Create customers table
    op.create_table(
        'customers',
        sa.Column('customer_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('tenant_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('name', sa.String(), nullable=False),
        sa.Column('phone', sa.String(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['tenant_id'], ['tenants.tenant_id'], ),
        sa.PrimaryKeyConstraint('customer_id'),
        sa.UniqueConstraint('tenant_id', 'phone', name='uq_customer_tenant_phone')
    )
    op.create_index(op.f('ix_customers_tenant_id'), 'customers', ['tenant_id'], unique=False)
    
    # Create inventory_items table
    op.create_table(
        'inventory_items',
        sa.Column('item_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('tenant_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('name', sa.String(), nullable=False),
        sa.Column('category', sa.String(), nullable=False),
        sa.Column('quantity', sa.Numeric(precision=10, scale=2), nullable=False),
        sa.Column('unit', sa.String(), nullable=False),
        sa.Column('cost_per_unit', sa.Numeric(precision=10, scale=2), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['tenant_id'], ['tenants.tenant_id'], ),
        sa.PrimaryKeyConstraint('item_id'),
        sa.UniqueConstraint('tenant_id', 'name', name='uq_inventory_tenant_name')
    )
    op.create_index(op.f('ix_inventory_items_tenant_id'), 'inventory_items', ['tenant_id'], unique=False)
    
    # Create recipes table
    op.create_table(
        'recipes',
        sa.Column('recipe_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('tenant_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('name', sa.String(), nullable=False),
        sa.Column('yield_per_batch', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['tenant_id'], ['tenants.tenant_id'], ),
        sa.PrimaryKeyConstraint('recipe_id'),
        sa.UniqueConstraint('tenant_id', 'name', name='uq_recipe_tenant_name')
    )
    op.create_index(op.f('ix_recipes_tenant_id'), 'recipes', ['tenant_id'], unique=False)
    
    # Create recipe_components table
    op.create_table(
        'recipe_components',
        sa.Column('component_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('recipe_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('item_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('quantity', sa.Numeric(precision=10, scale=2), nullable=False),
        sa.Column('type', sa.String(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['item_id'], ['inventory_items.item_id'], ),
        sa.ForeignKeyConstraint(['recipe_id'], ['recipes.recipe_id'], ),
        sa.PrimaryKeyConstraint('component_id')
    )
    op.create_index(op.f('ix_recipe_components_recipe_id'), 'recipe_components', ['recipe_id'], unique=False)
    op.create_index(op.f('ix_recipe_components_item_id'), 'recipe_components', ['item_id'], unique=False)
    
    # Create orders table
    op.create_table(
        'orders',
        sa.Column('order_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('tenant_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('customer_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('delivery_date', sa.Date(), nullable=False),
        sa.Column('status', sa.String(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['customer_id'], ['customers.customer_id'], ),
        sa.ForeignKeyConstraint(['tenant_id'], ['tenants.tenant_id'], ),
        sa.PrimaryKeyConstraint('order_id')
    )
    op.create_index(op.f('ix_orders_tenant_id'), 'orders', ['tenant_id'], unique=False)
    op.create_index(op.f('ix_orders_customer_id'), 'orders', ['customer_id'], unique=False)
    
    # Create order_items table
    op.create_table(
        'order_items',
        sa.Column('order_item_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('order_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('recipe_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('quantity', sa.Integer(), nullable=False),
        sa.Column('selling_price', sa.Numeric(precision=10, scale=2), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['order_id'], ['orders.order_id'], ),
        sa.ForeignKeyConstraint(['recipe_id'], ['recipes.recipe_id'], ),
        sa.PrimaryKeyConstraint('order_item_id')
    )
    op.create_index(op.f('ix_order_items_order_id'), 'order_items', ['order_id'], unique=False)
    op.create_index(op.f('ix_order_items_recipe_id'), 'order_items', ['recipe_id'], unique=False)
    
    # Create payments table
    op.create_table(
        'payments',
        sa.Column('payment_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('tenant_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('order_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('amount', sa.Numeric(precision=10, scale=2), nullable=False),
        sa.Column('method', sa.String(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['order_id'], ['orders.order_id'], ),
        sa.ForeignKeyConstraint(['tenant_id'], ['tenants.tenant_id'], ),
        sa.PrimaryKeyConstraint('payment_id')
    )
    op.create_index(op.f('ix_payments_tenant_id'), 'payments', ['tenant_id'], unique=False)
    op.create_index(op.f('ix_payments_order_id'), 'payments', ['order_id'], unique=False)
    
    # Create audit_logs table
    op.create_table(
        'audit_logs',
        sa.Column('log_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('tenant_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('table_name', sa.String(), nullable=False),
        sa.Column('record_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('operation_type', sa.String(), nullable=False),
        sa.Column('old_values', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column('new_values', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['tenant_id'], ['tenants.tenant_id'], ),
        sa.PrimaryKeyConstraint('log_id')
    )
    op.create_index(op.f('ix_audit_logs_tenant_id'), 'audit_logs', ['tenant_id'], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    # Drop tables in reverse order to respect foreign key constraints
    op.drop_index(op.f('ix_audit_logs_tenant_id'), table_name='audit_logs')
    op.drop_table('audit_logs')
    
    op.drop_index(op.f('ix_payments_order_id'), table_name='payments')
    op.drop_index(op.f('ix_payments_tenant_id'), table_name='payments')
    op.drop_table('payments')
    
    op.drop_index(op.f('ix_order_items_recipe_id'), table_name='order_items')
    op.drop_index(op.f('ix_order_items_order_id'), table_name='order_items')
    op.drop_table('order_items')
    
    op.drop_index(op.f('ix_orders_customer_id'), table_name='orders')
    op.drop_index(op.f('ix_orders_tenant_id'), table_name='orders')
    op.drop_table('orders')
    
    op.drop_index(op.f('ix_recipe_components_item_id'), table_name='recipe_components')
    op.drop_index(op.f('ix_recipe_components_recipe_id'), table_name='recipe_components')
    op.drop_table('recipe_components')
    
    op.drop_index(op.f('ix_recipes_tenant_id'), table_name='recipes')
    op.drop_table('recipes')
    
    op.drop_index(op.f('ix_inventory_items_tenant_id'), table_name='inventory_items')
    op.drop_table('inventory_items')
    
    op.drop_index(op.f('ix_customers_tenant_id'), table_name='customers')
    op.drop_table('customers')
    
    op.drop_index(op.f('ix_tenants_chat_id'), table_name='tenants')
    op.drop_table('tenants')
