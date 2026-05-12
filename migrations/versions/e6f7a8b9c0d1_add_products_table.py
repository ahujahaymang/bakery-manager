"""add products and product_variants tables

Revision ID: e6f7a8b9c0d1
Revises: d5e6f7a8b9c0
Create Date: 2026-05-12

"""
from alembic import op
import sqlalchemy as sa

revision = 'e6f7a8b9c0d1'
down_revision = 'd5e6f7a8b9c0'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'products',
        sa.Column('product_id', sa.String(36), primary_key=True),
        sa.Column('tenant_id', sa.String(36), sa.ForeignKey('tenants.tenant_id'), nullable=False),
        sa.Column('name', sa.String(), nullable=False),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('category', sa.String(), nullable=True),
        sa.Column('image_url', sa.String(), nullable=True),
        sa.Column('recipe_id', sa.String(36), sa.ForeignKey('recipes.recipe_id'), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.UniqueConstraint('tenant_id', 'name', name='uq_product_tenant_name'),
    )
    op.create_index('ix_products_tenant_id', 'products', ['tenant_id'])
    op.create_index('ix_products_recipe_id', 'products', ['recipe_id'])

    op.create_table(
        'product_variants',
        sa.Column('variant_id', sa.String(36), primary_key=True),
        sa.Column('product_id', sa.String(36), sa.ForeignKey('products.product_id'), nullable=False),
        sa.Column('size_label', sa.String(), nullable=False),
        sa.Column('price', sa.Numeric(10, 2), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.UniqueConstraint('product_id', 'size_label', name='uq_variant_product_size'),
    )
    op.create_index('ix_product_variants_product_id', 'product_variants', ['product_id'])


def downgrade() -> None:
    op.drop_index('ix_product_variants_product_id', 'product_variants')
    op.drop_table('product_variants')
    op.drop_index('ix_products_recipe_id', 'products')
    op.drop_index('ix_products_tenant_id', 'products')
    op.drop_table('products')
