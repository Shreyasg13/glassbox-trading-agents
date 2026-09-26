"""compliance_events (S3 T8)

Revision ID: 0006
Revises: 0005
Create Date: 2026-09-26
"""
from alembic import op
import sqlalchemy as sa

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "compliance_events",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("run_id", sa.String(), nullable=True),
        sa.Column("channel", sa.String(), nullable=False),
        sa.Column("rule_id", sa.String(), nullable=False),
        sa.Column("matched_text", sa.String(300), nullable=False),
        sa.Column("action", sa.String(), nullable=False),  # blocked | rewritten | flagged
        sa.Column("created_at", sa.String(), nullable=False),
    )
    op.create_index("ix_compliance_events_created_at", "compliance_events", ["created_at"])
    op.create_index("ix_compliance_events_run_id", "compliance_events", ["run_id"])


def downgrade() -> None:
    op.drop_index("ix_compliance_events_run_id", table_name="compliance_events")
    op.drop_index("ix_compliance_events_created_at", table_name="compliance_events")
    op.drop_table("compliance_events")
