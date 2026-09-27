"""quarantine_items (S3 T5)

Revision ID: 0007
Revises: 0006
Create Date: 2026-09-26
"""
from alembic import op
import sqlalchemy as sa

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "quarantine_items",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("channel", sa.String(), nullable=False),
        sa.Column("run_id", sa.String(), nullable=True),
        sa.Column("content_ref", sa.String(), nullable=False),
        sa.Column("stage", sa.String(), nullable=False),  # A6 | A7
        sa.Column("status", sa.String(), nullable=False),  # pending | approved | rejected | shadow
        sa.Column("reasons_json", sa.Text(), nullable=False, default="[]"),
        sa.Column("created_at", sa.String(), nullable=False),
        sa.Column("reviewer_id", sa.String(), nullable=True),
        sa.Column("review_note", sa.String(), nullable=True),
        sa.Column("reviewed_at", sa.String(), nullable=True),
    )
    op.create_index("ix_quarantine_items_status", "quarantine_items", ["status"])
    op.create_index("ix_quarantine_items_created_at", "quarantine_items", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_quarantine_items_created_at", table_name="quarantine_items")
    op.drop_index("ix_quarantine_items_status", table_name="quarantine_items")
    op.drop_table("quarantine_items")