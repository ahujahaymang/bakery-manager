"""add_recipe_name_to_order_items

Revision ID: a6aa3be8a482
Revises: 1cb7686b9b96
Create Date: 2026-04-15 10:24:37.921186

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a6aa3be8a482'
down_revision: Union[str, Sequence[str], None] = '1cb7686b9b96'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # Add recipe_name column to order_items table (nullable initially)
    op.add_column('order_items', sa.Column('recipe_name', sa.String(length=255), nullable=True))
    
    # Populate recipe_name from recipes table for existing records with recipe_id
    op.execute("""
        UPDATE order_items 
        SET recipe_name = recipes.name 
        FROM recipes 
        WHERE order_items.recipe_id = recipes.recipe_id
    """)
    
    # For any remaining NULL values (orders without recipes), set a default
    op.execute("""
        UPDATE order_items 
        SET recipe_name = 'Unknown Item'
        WHERE recipe_name IS NULL
    """)
    
    # Make recipe_name NOT NULL after populating
    op.alter_column('order_items', 'recipe_name', nullable=False)


def downgrade() -> None:
    """Downgrade schema."""
    # Remove recipe_name column from order_items table
    op.drop_column('order_items', 'recipe_name')
