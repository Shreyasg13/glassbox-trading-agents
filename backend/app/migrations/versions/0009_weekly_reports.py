"""weekly_reports: admin-published weekly discrepancy reports (S3 T13)

Revision ID: 0009
Revises: 0008
Create Date: 2026-09-29
"""
from alembic import op
import sqlalchemy as sa

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "weekly_reports",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("week_start", sa.String(), nullable=False, unique=True),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("body_json", sa.Text(), nullable=False),
        sa.Column("created_at", sa.String(), nullable=False),
        sa.Column("published_at", sa.String(), nullable=True),
        sa.Column("published_by", sa.String(), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("weekly_reports")
