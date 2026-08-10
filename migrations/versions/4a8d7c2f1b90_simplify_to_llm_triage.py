"""simplify to LLM triage

Revision ID: 4a8d7c2f1b90
Revises: d231429e0e46
Create Date: 2026-08-09
"""
import sqlalchemy as sa
from alembic import op

revision = "4a8d7c2f1b90"
down_revision = "d231429e0e46"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "email_classification",
        sa.Column("cleanup_candidate", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column(
        "email_classification",
        sa.Column("cleanup_reason", sa.Text(), nullable=True),
    )
    op.create_index(
        "ix_email_classification_cleanup_candidate",
        "email_classification",
        ["cleanup_candidate"],
        unique=False,
    )
    op.drop_table("email_training_event")


def downgrade() -> None:
    op.create_table(
        "email_training_event",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("message_id", sa.Integer(), sa.ForeignKey("email_message.id"), nullable=False),
        sa.Column("label", sa.String(length=40), nullable=False),
        sa.Column("source", sa.String(length=40), nullable=False),
        sa.Column("weight", sa.Float(), nullable=False),
        sa.Column("trusted", sa.Boolean(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_email_training_event_message_id", "email_training_event", ["message_id"])
    op.create_index("ix_email_training_event_label", "email_training_event", ["label"])
    op.drop_index("ix_email_classification_cleanup_candidate", table_name="email_classification")
    op.drop_column("email_classification", "cleanup_reason")
    op.drop_column("email_classification", "cleanup_candidate")
