"""call_outcomes: forward-only scoring results (S3 T12a)

Revision ID: 0008
Revises: 0007
Create Date: 2026-09-28
"""
from alembic import op
import sqlalchemy as sa

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "call_outcomes",
        sa.Column("call_id", sa.String(), primary_key=True),
        sa.Column("horizon", sa.Integer(), primary_key=True),
        sa.Column("evaluated_at", sa.String(), nullable=False),
        sa.Column("outcome_json", sa.Text(), nullable=False),
        sa.Column("score", sa.Float(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("call_outcomes")