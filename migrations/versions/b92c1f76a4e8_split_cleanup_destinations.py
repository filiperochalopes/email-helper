"""split cleanup destinations and persist reply headers

Revision ID: b92c1f76a4e8
Revises: 7cb2a947d831
Create Date: 2026-08-13
"""
import sqlalchemy as sa
from alembic import op

revision = "b92c1f76a4e8"
down_revision = "7cb2a947d831"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("email_message", sa.Column("in_reply_to_header", sa.String(998)))
    op.add_column("email_message", sa.Column("references_json", sa.JSON()))
    op.add_column(
        "email_classification",
        sa.Column("cleanup_action", sa.String(20), nullable=False, server_default="none"),
    )
    op.execute(
        "UPDATE email_classification SET cleanup_action = 'trash' "
        "WHERE cleanup_candidate IS TRUE"
    )
    op.execute(
        "UPDATE email_classification SET category = 'revisar', "
        "cleanup_action = 'none', cleanup_candidate = FALSE "
        "WHERE message_id IN (SELECT message_id FROM human_review WHERE status = 'pending')"
    )
    op.drop_index("ix_email_classification_cleanup_pending", table_name="email_classification")
    op.execute(
        "CREATE INDEX ix_email_classification_cleanup_action_pending "
        "ON email_classification (cleanup_action, message_id) "
        "WHERE cleanup_action <> 'none'"
    )


def downgrade() -> None:
    op.drop_index(
        "ix_email_classification_cleanup_action_pending",
        table_name="email_classification",
    )
    op.execute(
        "CREATE INDEX ix_email_classification_cleanup_pending ON email_classification "
        "(message_id) WHERE cleanup_candidate"
    )
    op.drop_column("email_classification", "cleanup_action")
    op.drop_column("email_message", "references_json")
    op.drop_column("email_message", "in_reply_to_header")
