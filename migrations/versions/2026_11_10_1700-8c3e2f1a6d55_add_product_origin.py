"""add product origin

Revision ID: 8c3e2f1a6d55
Revises: 5b1d0c7e9a42
Create Date: 2026-11-10 17:00:00

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "8c3e2f1a6d55"
down_revision: str | Sequence[str] | None = "5b1d0c7e9a42"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column("products", sa.Column("origin", sa.String(length=100), nullable=True))
    # Lots published before this change come from their producer's municipality too.
    op.execute(
        "UPDATE products SET origin = users.municipality "
        "FROM users WHERE users.id = products.producer_id"
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("products", "origin")
