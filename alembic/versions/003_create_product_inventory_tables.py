"""create product inventories and seasonal availabilities tables

Revision ID: 003
Revises: 002
Create Date: 2026-10-25
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "003"
down_revision = "002"
branch_labels = None
depends_on = None

availability_status_enum = postgresql.ENUM(
    "available",
    "out_of_stock",
    "coming_soon",
    "out_of_season",
    name="availability_status_enum",
    create_type=False,
)


def upgrade() -> None:
    """Create availability_status_enum, product_inventories, and seasonal_availabilities tables."""
    availability_status_enum.create(op.get_bind(), checkfirst=True)

    op.create_table(
        "product_inventories",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("product_id", postgresql.UUID(as_uuid=True), nullable=False, unique=True),
        sa.Column("product_name", sa.String(length=255), nullable=True),
        sa.Column("stock_quantity", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("reserved_quantity", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("low_stock_threshold", sa.Integer(), nullable=False, server_default=sa.text("5")),
        sa.Column("is_seasonal", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("season_start", sa.Date(), nullable=True),
        sa.Column("season_end", sa.Date(), nullable=True),
        sa.Column(
            "status",
            availability_status_enum,
            nullable=False,
            server_default=sa.text("'out_of_stock'"),
        ),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now(), onupdate=sa.func.now()),
    )
    op.create_index(
        "ix_product_inventories_product_id",
        "product_inventories",
        ["product_id"],
        unique=True,
    )

    op.create_table(
        "seasonal_availabilities",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "inventory_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("product_inventories.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("season_name", sa.String(length=100), nullable=True),
        sa.Column("start_date", sa.Date(), nullable=False),
        sa.Column("end_date", sa.Date(), nullable=False),
    )
    op.create_index(
        "ix_seasonal_availabilities_inventory_id",
        "seasonal_availabilities",
        ["inventory_id"],
        unique=False,
    )


def downgrade() -> None:
    """Drop seasonal_availabilities, product_inventories, and availability_status_enum."""
    op.drop_index("ix_seasonal_availabilities_inventory_id", table_name="seasonal_availabilities")
    op.drop_table("seasonal_availabilities")
    op.drop_index("ix_product_inventories_product_id", table_name="product_inventories")
    op.drop_table("product_inventories")
    availability_status_enum.drop(op.get_bind(), checkfirst=True)
