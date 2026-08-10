"""optimize search and LLM audit

Revision ID: 7cb2a947d831
Revises: 4a8d7c2f1b90
Create Date: 2026-08-10
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "7cb2a947d831"
down_revision = "4a8d7c2f1b90"
branch_labels = None
depends_on = None

SEARCH_EXPRESSION = (
    "setweight(to_tsvector('simple', coalesce(subject, '')), 'A') || "
    "setweight(to_tsvector('simple', coalesce(from_name, '') || ' ' || "
    "coalesce(from_email, '')), 'B') || "
    "setweight(to_tsvector('simple', coalesce(normalized_text, '')), 'C')"
)


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    op.add_column(
        "email_message",
        sa.Column(
            "search_vector",
            postgresql.TSVECTOR(),
            sa.Computed(SEARCH_EXPRESSION, persisted=True),
            nullable=True,
        ),
    )
    op.create_index(
        "ix_email_message_search_vector", "email_message", ["search_vector"],
        unique=False, postgresql_using="gin",
    )
    op.execute(
        "CREATE INDEX ix_email_message_subject_trgm ON email_message "
        "USING gin (subject gin_trgm_ops)"
    )
    op.execute(
        "CREATE INDEX ix_email_message_from_name_trgm ON email_message "
        "USING gin (from_name gin_trgm_ops)"
    )
    op.execute(
        "CREATE INDEX ix_email_message_from_email_trgm ON email_message "
        "USING gin (from_email gin_trgm_ops)"
    )

    op.drop_index("ix_email_message_account_id", table_name="email_message")
    op.drop_index("ix_email_message_provider_message_id", table_name="email_message")
    op.drop_index("ix_email_message_message_id_header", table_name="email_message")
    op.drop_index("ix_email_message_provider_thread_id", table_name="email_message")
    op.execute(
        "CREATE INDEX ix_email_message_account_header ON email_message "
        "(account_id, message_id_header) WHERE message_id_header IS NOT NULL"
    )
    op.execute(
        "CREATE INDEX ix_email_message_account_thread_date ON email_message "
        "(account_id, provider_thread_id, date DESC) WHERE provider_thread_id IS NOT NULL"
    )
    op.create_index("ix_email_message_date", "email_message", [sa.text("date DESC")])
    op.create_index("ix_email_message_created_at", "email_message", [sa.text("created_at DESC")])

    op.drop_index("ix_email_classification_message_id", table_name="email_classification")
    op.create_index(
        "uq_email_classification_message_id", "email_classification", ["message_id"], unique=True
    )
    op.drop_index(
        "ix_email_classification_cleanup_candidate", table_name="email_classification"
    )
    op.execute(
        "CREATE INDEX ix_email_classification_cleanup_pending ON email_classification "
        "(message_id) WHERE cleanup_candidate"
    )
    op.execute(
        "CREATE INDEX ix_email_classification_priority_score ON email_classification "
        "(priority, importance_score DESC)"
    )
    op.execute(
        "CREATE INDEX ix_human_review_pending_created ON human_review "
        "(status, created_at DESC)"
    )

    op.alter_column("email_classification", "model_name", new_column_name="llm_model")
    op.alter_column(
        "email_classification", "llm_model", type_=sa.String(length=200),
        existing_type=sa.String(length=100), existing_nullable=True,
    )
    op.add_column("email_classification", sa.Column("llm_provider", sa.String(40)))
    op.add_column("email_classification", sa.Column("llm_prompt_version", sa.String(50)))
    op.add_column("email_classification", sa.Column("llm_raw_result", sa.JSON()))
    op.add_column("email_classification", sa.Column("llm_raw_response", sa.Text()))
    op.add_column("email_classification", sa.Column("llm_input_tokens", sa.Integer()))
    op.add_column("email_classification", sa.Column("llm_output_tokens", sa.Integer()))
    op.add_column("email_classification", sa.Column("llm_latency_ms", sa.Integer()))
    op.add_column("email_classification", sa.Column("llm_error", sa.Text()))
    op.execute(
        "UPDATE email_classification SET llm_provider = 'legacy', llm_model = NULL "
        "WHERE llm_model = 'rules+sgd+ollama'"
    )


def downgrade() -> None:
    for column in (
        "llm_error", "llm_latency_ms", "llm_output_tokens", "llm_input_tokens",
        "llm_raw_response", "llm_raw_result", "llm_prompt_version", "llm_provider",
    ):
        op.drop_column("email_classification", column)
    op.alter_column(
        "email_classification", "llm_model", type_=sa.String(length=100),
        existing_type=sa.String(length=200), existing_nullable=True,
    )
    op.alter_column("email_classification", "llm_model", new_column_name="model_name")

    op.drop_index("ix_human_review_pending_created", table_name="human_review")
    op.drop_index("ix_email_classification_priority_score", table_name="email_classification")
    op.drop_index("ix_email_classification_cleanup_pending", table_name="email_classification")
    op.create_index(
        "ix_email_classification_cleanup_candidate", "email_classification",
        ["cleanup_candidate"], unique=False,
    )
    op.drop_index("uq_email_classification_message_id", table_name="email_classification")
    op.create_index(
        "ix_email_classification_message_id", "email_classification", ["message_id"], unique=False
    )

    op.drop_index("ix_email_message_created_at", table_name="email_message")
    op.drop_index("ix_email_message_date", table_name="email_message")
    op.drop_index("ix_email_message_account_thread_date", table_name="email_message")
    op.drop_index("ix_email_message_account_header", table_name="email_message")
    op.create_index(
        "ix_email_message_provider_thread_id", "email_message", ["provider_thread_id"]
    )
    op.create_index("ix_email_message_message_id_header", "email_message", ["message_id_header"])
    op.create_index(
        "ix_email_message_provider_message_id", "email_message", ["provider_message_id"]
    )
    op.create_index("ix_email_message_account_id", "email_message", ["account_id"])
    op.drop_index("ix_email_message_from_email_trgm", table_name="email_message")
    op.drop_index("ix_email_message_from_name_trgm", table_name="email_message")
    op.drop_index("ix_email_message_subject_trgm", table_name="email_message")
    op.drop_index("ix_email_message_search_vector", table_name="email_message")
    op.drop_column("email_message", "search_vector")
    op.execute("DROP EXTENSION IF EXISTS pg_trgm")
