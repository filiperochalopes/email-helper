from typing import Any, Literal, TypedDict


class EmailAgentState(TypedDict, total=False):
    account_id: int
    account_email: str
    provider: Literal["gmail_api", "imap"]
    provider_message_id: str
    provider_thread_id: str | None
    email_agent_id: str
    db_message_id: int
    mailbox: str
    from_email: str
    from_name: str | None
    to: list[str]
    cc: list[str]
    subject: str
    date: str
    message_id_header: str | None
    normalized_text: str
    attachments: list[dict[str, Any]]
    has_list_unsubscribe: bool
    is_sent_by_user: bool
    current_provider_labels: list[str]
    current_ai_labels: list[str]
    signals: dict[str, Any]
    spam_score: float
    spam_reason: str
    importance_score: float
    importance_reason: str
    priority: Literal["P0", "P1", "P2", "ignore"]
    category: str
    confidence: float
    action_required: bool
    cleanup_candidate: bool
    cleanup_reason: str | None
    is_followup_waiting_response: bool
    followup_reason: str | None
    suggested_labels: list[str]
    suggested_actions: list[dict[str, Any]]
    needs_human_review: bool
    human_review_reason: str | None
    digest_include: bool
    digest_priority: Literal["P0", "P1", "P2", "none"]
    digest_summary: str | None
    applied_actions: list[dict[str, Any]]
    errors: list[str]
