"""make recipe_id nullable in order_items

Revision ID: 1cb7686b9b96
Revises: 07c9fd7f518a
Create Date: 2026-04-15 08:37:02.343834

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '1cb7686b9b96'
down_revision: Union[str, Sequence[str], None] = '07c9fd7f518a'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # Make recipe_id nullable in order_items table
    op.alter_column('order_items', 'recipe_id',
                    existing_type=sa.UUID(),
                    nullable=True)


def downgrade() -> None:
    """Downgrade schema."""
    # Make recipe_id NOT NULL again
    op.alter_column('order_items', 'recipe_id',
                    existing_type=sa.UUID(),
                    nullable=False)
