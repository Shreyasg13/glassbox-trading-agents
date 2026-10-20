"""studio_drafts: the Daily Studio's share-ready drafts and weekly recaps

Revision ID: 0009
Revises: 0008
Create Date: 2026-10-19
"""
from alembic import op
import sqlalchemy as sa

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "studio_drafts",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("kind", sa.String(), nullable=False),
        sa.Column("date", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("payload_json", sa.Text(), nullable=False),
        sa.Column("created_at", sa.String(), nullable=False),
        sa.Column("updated_at", sa.String(), nullable=False),
    )
    op.create_index("ix_studio_drafts_date", "studio_drafts", ["date"])


def downgrade() -> None:
    op.drop_index("ix_studio_drafts_date", table_name="studio_drafts")
    op.drop_table("studio_drafts")
