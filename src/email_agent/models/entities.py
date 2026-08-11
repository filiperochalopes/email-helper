"""Todos os models SQLAlchemy do email-helper.

Mantidos num único módulo para facilitar autogenerate do Alembic; o pacote
``email_agent.models`` reexporta tudo.
"""
from datetime import date, datetime

from sqlalchemy import (
    JSON,
    Boolean,
    Computed,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import TSVECTOR
from sqlalchemy.orm import Mapped, mapped_column, relationship

from email_agent.models.db import Base


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class EmailAccount(TimestampMixin, Base):
    __tablename__ = "email_account"

    id: Mapped[int] = mapped_column(primary_key=True)
    provider: Mapped[str] = mapped_column(String(20))  # gmail_api | imap
    email_address: Mapped[str] = mapped_column(String(320), unique=True, index=True)
    display_name: Mapped[str | None] = mapped_column(String(200))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    priority_weight: Mapped[float] = mapped_column(Float, default=1.0)
    # gmail_api: nome do arquivo de token; imap: referência lógica no YAML local
    credentials_ref: Mapped[str | None] = mapped_column(String(500))
    imap_host: Mapped[str | None] = mapped_column(String(255))
    imap_port: Mapped[int | None] = mapped_column(Integer, default=993)
    auth_status: Mapped[str] = mapped_column(String(20), default="ok")  # ok|reauth_required|error
    cursors: Mapped[list["MailboxCursor"]] = relationship(back_populates="account")


class MailboxCursor(Base):
    __tablename__ = "mailbox_cursor"
    __table_args__ = (UniqueConstraint("account_id", "mailbox"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("email_account.id"), index=True)
    mailbox: Mapped[str] = mapped_column(String(255))
    uidvalidity: Mapped[int | None] = mapped_column(Integer)
    last_uid: Mapped[int | None] = mapped_column(Integer)
    last_history_id: Mapped[str | None] = mapped_column(String(50))
    last_sync_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    sync_status: Mapped[str] = mapped_column(String(20), default="idle")
    error_message: Mapped[str | None] = mapped_column(Text)
    account: Mapped[EmailAccount] = relationship(back_populates="cursors")


class EmailMessage(TimestampMixin, Base):
    __tablename__ = "email_message"
    __table_args__ = (UniqueConstraint("account_id", "provider_message_id", "mailbox"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    email_agent_id: Mapped[str] = mapped_column(String(30), unique=True, index=True)  # E-YYYYMMDD-000001
    account_id: Mapped[int] = mapped_column(ForeignKey("email_account.id"))
    provider_message_id: Mapped[str] = mapped_column(String(255))
    provider_thread_id: Mapped[str | None] = mapped_column(String(255))
    message_id_header: Mapped[str | None] = mapped_column(String(998))
    mailbox: Mapped[str] = mapped_column(String(255))
    from_email: Mapped[str | None] = mapped_column(String(320), index=True)
    from_name: Mapped[str | None] = mapped_column(String(200))
    to_json: Mapped[list | None] = mapped_column(JSON)
    cc_json: Mapped[list | None] = mapped_column(JSON)
    subject: Mapped[str | None] = mapped_column(Text)
    date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    received_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    snippet: Mapped[str | None] = mapped_column(Text)
    normalized_text: Mapped[str | None] = mapped_column(Text)
    search_vector: Mapped[str | None] = mapped_column(
        TSVECTOR,
        Computed(
            "setweight(to_tsvector('simple', coalesce(subject, '')), 'A') || "
            "setweight(to_tsvector('simple', coalesce(from_name, '') || ' ' || "
            "coalesce(from_email, '')), 'B') || "
            "setweight(to_tsvector('simple', coalesce(normalized_text, '')), 'C')",
            persisted=True,
        ),
        deferred=True,
    )
    normalized_text_hash: Mapped[str | None] = mapped_column(String(64), index=True)
    has_attachment: Mapped[bool] = mapped_column(Boolean, default=False)
    is_read: Mapped[bool] = mapped_column(Boolean, default=False)
    is_sent_by_user: Mapped[bool] = mapped_column(Boolean, default=False)
    raw_labels: Mapped[list | None] = mapped_column(JSON)
    ai_labels: Mapped[list | None] = mapped_column(JSON)

    attachments: Mapped[list["EmailAttachment"]] = relationship(back_populates="message")
    classifications: Mapped[list["EmailClassification"]] = relationship(back_populates="message")


class EmailAttachment(Base):
    __tablename__ = "email_attachment"

    id: Mapped[int] = mapped_column(primary_key=True)
    message_id: Mapped[int] = mapped_column(ForeignKey("email_message.id"), index=True)
    filename: Mapped[str | None] = mapped_column(String(500))
    content_type: Mapped[str | None] = mapped_column(String(200))
    size_bytes: Mapped[int | None] = mapped_column(Integer)
    sha256: Mapped[str | None] = mapped_column(String(64), index=True)
    extracted_text: Mapped[str | None] = mapped_column(Text)
    extraction_status: Mapped[str] = mapped_column(String(20), default="pending")
    message: Mapped[EmailMessage] = relationship(back_populates="attachments")


class EmailClassification(Base):
    __tablename__ = "email_classification"

    id: Mapped[int] = mapped_column(primary_key=True)
    message_id: Mapped[int] = mapped_column(ForeignKey("email_message.id"))
    spam_score: Mapped[float | None] = mapped_column(Float)
    spam_reason: Mapped[str | None] = mapped_column(Text)
    importance_score: Mapped[float | None] = mapped_column(Float)
    importance_reason: Mapped[str | None] = mapped_column(Text)
    priority: Mapped[str | None] = mapped_column(String(10))  # P0|P1|P2|ignore
    category: Mapped[str | None] = mapped_column(String(40), index=True)
    action_required: Mapped[bool] = mapped_column(Boolean, default=False)
    cleanup_candidate: Mapped[bool] = mapped_column(Boolean, default=False)
    cleanup_reason: Mapped[str | None] = mapped_column(Text)
    deadline: Mapped[date | None] = mapped_column(Date)
    digest_summary: Mapped[str | None] = mapped_column(Text)
    suggested_labels: Mapped[list | None] = mapped_column(JSON)
    llm_provider: Mapped[str | None] = mapped_column(String(40))
    llm_model: Mapped[str | None] = mapped_column(String(200))
    llm_prompt_version: Mapped[str | None] = mapped_column(String(50))
    llm_raw_result: Mapped[dict | None] = mapped_column(JSON)
    llm_raw_response: Mapped[str | None] = mapped_column(Text)
    llm_input_tokens: Mapped[int | None] = mapped_column(Integer)
    llm_output_tokens: Mapped[int | None] = mapped_column(Integer)
    llm_latency_ms: Mapped[int | None] = mapped_column(Integer)
    llm_error: Mapped[str | None] = mapped_column(Text)
    confidence: Mapped[float | None] = mapped_column(Float)
    classified_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    message: Mapped[EmailMessage] = relationship(back_populates="classifications")


class EmailRule(Base):
    __tablename__ = "email_rule"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200), unique=True)
    rule_type: Mapped[str] = mapped_column(String(20))  # spam|label|importance|silence|followup
    condition_json: Mapped[dict] = mapped_column(JSON)
    action_json: Mapped[dict] = mapped_column(JSON)
    confidence: Mapped[float] = mapped_column(Float, default=1.0)
    created_by: Mapped[str] = mapped_column(String(20), default="user")  # user|web|agent|system
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    requires_review: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class EmailActionLog(Base):
    __tablename__ = "email_action_log"

    id: Mapped[int] = mapped_column(primary_key=True)
    message_id: Mapped[int] = mapped_column(ForeignKey("email_message.id"), index=True)
    action_type: Mapped[str] = mapped_column(String(30))
    action_payload: Mapped[dict | None] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(20), default="pending")  # pending|success|error|skipped
    error: Mapped[str | None] = mapped_column(Text)
    idempotency_key: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class EmailUserEvent(Base):
    __tablename__ = "email_user_event"

    id: Mapped[int] = mapped_column(primary_key=True)
    message_id: Mapped[int] = mapped_column(ForeignKey("email_message.id"), index=True)
    event_type: Mapped[str] = mapped_column(String(40), index=True)
    previous_labels: Mapped[list | None] = mapped_column(JSON)
    new_labels: Mapped[list | None] = mapped_column(JSON)
    previous_mailbox: Mapped[str | None] = mapped_column(String(255))
    new_mailbox: Mapped[str | None] = mapped_column(String(255))
    source: Mapped[str] = mapped_column(String(30), default="sync_diff")
    confidence: Mapped[float] = mapped_column(Float, default=1.0)
    detected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class HumanReview(Base):
    __tablename__ = "human_review"

    id: Mapped[int] = mapped_column(primary_key=True)
    message_id: Mapped[int] = mapped_column(ForeignKey("email_message.id"), index=True)
    review_type: Mapped[str] = mapped_column(String(40))
    prompt_text: Mapped[str | None] = mapped_column(Text)
    proposed_action_json: Mapped[dict | None] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(30), default="pending", index=True)
    decision_payload: Mapped[dict | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class DailyDigest(Base):
    __tablename__ = "daily_digest"

    id: Mapped[int] = mapped_column(primary_key=True)
    digest_date: Mapped[date] = mapped_column(Date, index=True)
    sent_to: Mapped[str | None] = mapped_column(String(30))
    body: Mapped[str | None] = mapped_column(Text)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(20), default="pending")


# Índices alinhados às consultas reais. Os índices simples redundantes foram
# removidos; o prefixo da unique de deduplicação já atende busca por account_id.
Index(
    "ix_email_message_account_header",
    EmailMessage.account_id,
    EmailMessage.message_id_header,
    postgresql_where=text("message_id_header IS NOT NULL"),
)
Index(
    "ix_email_message_account_thread_date",
    EmailMessage.account_id,
    EmailMessage.provider_thread_id,
    EmailMessage.date.desc(),
    postgresql_where=text("provider_thread_id IS NOT NULL"),
)
Index("ix_email_message_date", EmailMessage.date.desc())
Index("ix_email_message_created_at", EmailMessage.created_at.desc())
Index("ix_email_message_search_vector", EmailMessage.search_vector, postgresql_using="gin")
Index(
    "ix_email_message_subject_trgm",
    EmailMessage.subject,
    postgresql_using="gin",
    postgresql_ops={"subject": "gin_trgm_ops"},
)
Index(
    "ix_email_message_from_name_trgm",
    EmailMessage.from_name,
    postgresql_using="gin",
    postgresql_ops={"from_name": "gin_trgm_ops"},
)
Index(
    "ix_email_message_from_email_trgm",
    EmailMessage.from_email,
    postgresql_using="gin",
    postgresql_ops={"from_email": "gin_trgm_ops"},
)
Index(
    "uq_email_classification_message_id",
    EmailClassification.message_id,
    unique=True,
)
Index(
    "ix_email_classification_cleanup_pending",
    EmailClassification.message_id,
    postgresql_where=text("cleanup_candidate"),
)
Index(
    "ix_email_classification_priority_score",
    EmailClassification.priority,
    EmailClassification.importance_score.desc(),
)
Index(
    "ix_human_review_pending_created",
    HumanReview.status,
    HumanReview.created_at.desc(),
)
