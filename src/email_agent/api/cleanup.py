"""API local da fila de limpeza e das ações humanas em lote."""

from typing import Literal

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from email_agent.actions.archive_actions import archive_message
from email_agent.actions.delete_actions import trash_message
from email_agent.models import EmailAccount, EmailClassification, EmailMessage, db_session
from email_agent.search import email_search_statement

router = APIRouter(prefix="/api/cleanup", tags=["cleanup"])


class BulkActionRequest(BaseModel):
    action: Literal["archive", "trash"]
    ids: list[str] = Field(min_length=1, max_length=200)
    confirmed: bool = False


def _candidate_payload(
    msg: EmailMessage,
    classification: EmailClassification | None,
    account: EmailAccount,
) -> dict:
    return {
        "id": msg.email_agent_id,
        "account": account.email_address,
        "provider": account.provider,
        "from_email": msg.from_email or "",
        "from_name": msg.from_name or "",
        "subject": msg.subject or "(sem assunto)",
        "snippet": (msg.snippet or "")[:300],
        "date": msg.date.isoformat() if msg.date else None,
        "is_read": msg.is_read,
        "has_attachment": msg.has_attachment,
        "category": classification.category if classification else None,
        "priority": classification.priority if classification else None,
        "cleanup_candidate": bool(
            classification and classification.cleanup_candidate
        ),
        "cleanup_reason": classification.cleanup_reason if classification else None,
        "confidence": classification.confidence if classification else None,
    }


@router.get("/messages")
def list_cleanup_messages(
    page: int = Query(1, ge=1),
    page_size: int = Query(40, ge=1, le=100),
    mode: Literal["candidates", "all"] = "candidates",
    query: str | None = Query(None, max_length=300),
    account: str | None = Query(None, max_length=320),
    category: str | None = Query(None, max_length=40),
) -> dict:
    """Lista somente metadados e snippet; o corpo completo nunca vai para a fila."""
    statement = email_search_statement(
        query=query,
        category=category,
        limit=page_size,
    )
    statement = statement.join(
        EmailAccount, EmailAccount.id == EmailMessage.account_id
    ).add_columns(EmailAccount)
    statement = statement.where(
        EmailMessage.mailbox == "INBOX",
        EmailMessage.is_sent_by_user.is_(False),
    )
    if mode == "candidates":
        statement = statement.where(EmailClassification.cleanup_candidate.is_(True))
    if account:
        statement = statement.where(EmailAccount.email_address == account)

    count_statement = select(func.count()).select_from(
        statement.order_by(None).limit(None).offset(None).subquery()
    )
    statement = statement.offset((page - 1) * page_size)

    with db_session() as session:
        total = session.execute(count_statement).scalar_one()
        rows = session.execute(statement).all()
        accounts = session.execute(
            select(EmailAccount.email_address)
            .where(EmailAccount.is_active.is_(True))
            .order_by(EmailAccount.email_address)
        ).scalars().all()

    items = [
        _candidate_payload(message, classification, item_account)
        for message, classification, _relevance, item_account in rows
    ]
    return {
        "items": items,
        "page": page,
        "page_size": page_size,
        "total": total,
        "has_more": page * page_size < total,
        "accounts": list(accounts),
    }


@router.post("/bulk-action")
def bulk_action(request: BulkActionRequest) -> dict:
    """Executa somente uma ação explicitamente confirmada, isolando falhas por item."""
    if not request.confirmed:
        raise HTTPException(status_code=400, detail="A confirmação explícita é obrigatória.")

    action = archive_message if request.action == "archive" else trash_message
    unique_ids = list(dict.fromkeys(request.ids))
    results = []
    for email_agent_id in unique_ids:
        status = action(email_agent_id)
        results.append(
            {
                "id": email_agent_id,
                "status": status,
                "success": status in {"archived", "trashed", "already"},
            }
        )
    succeeded = sum(item["success"] for item in results)
    return {
        "action": request.action,
        "requested": len(unique_ids),
        "succeeded": succeeded,
        "failed": len(unique_ids) - succeeded,
        "results": results,
    }
