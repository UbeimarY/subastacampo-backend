"""add automatic bidding

Revision ID: 5b1d0c7e9a42
Revises: 34835b6e495a
Create Date: 2026-11-10 15:30:00

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "5b1d0c7e9a42"
down_revision: str | Sequence[str] | None = "34835b6e495a"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "auto_bids",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("auction_id", sa.Uuid(), nullable=False),
        sa.Column("bidder_id", sa.Uuid(), nullable=False),
        sa.Column("max_amount", sa.BigInteger(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("max_amount > 0", name=op.f("ck_auto_bids_max_amount_positive")),
        sa.ForeignKeyConstraint(
            ["auction_id"], ["auctions.id"], name=op.f("fk_auto_bids_auction_id_auctions")
        ),
        sa.ForeignKeyConstraint(
            ["bidder_id"], ["users.id"], name=op.f("fk_auto_bids_bidder_id_users")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_auto_bids")),
        sa.UniqueConstraint(
            "auction_id", "bidder_id", name=op.f("uq_auto_bids_auction_id_bidder_id")
        ),
    )
    op.create_index(op.f("ix_auto_bids_auction_id"), "auto_bids", ["auction_id"], unique=False)
    op.add_column(
        "bids",
        sa.Column("automatic", sa.Boolean(), server_default=sa.text("false"), nullable=False),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("bids", "automatic")
    op.drop_index(op.f("ix_auto_bids_auction_id"), table_name="auto_bids")
    op.drop_table("auto_bids")
