"""API local da fila de limpeza e das ações humanas em lote."""

from datetime import date
from typing import Literal

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import case, func, select

from email_agent.actions.archive_actions import archive_message
from email_agent.actions.delete_actions import trash_message
from email_agent.models import (
    EmailAccount,
    EmailClassification,
    EmailMessage,
    EmailRule,
    db_session,
)
from email_agent.search import email_search_statement

router = APIRouter(prefix="/api/cleanup", tags=["cleanup"])


class BulkActionRequest(BaseModel):
    action: Literal["archive", "trash"]
    ids: list[str] = Field(min_length=1, max_length=200)
    confirmed: bool = False


class BlacklistRequest(BaseModel):
    target: Literal["sender", "domain"]


PriorityFilter = Literal["P0", "P1", "P2", "ignore"]
SortOrder = Literal["newest", "oldest", "priority"]


def _message_statement(
    *,
    page_size: int,
    mode: Literal["candidates", "all"] = "candidates",
    query: str | None = None,
    account: str | None = None,
    category: str | None = None,
    priority: PriorityFilter | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    sort: SortOrder = "newest",
):
    """Monta a mesma consulta para listagem e seleção, sem expor o corpo."""
    statement = email_search_statement(
        query=query,
        category=category,
        priority=priority,
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
    if date_from:
        statement = statement.where(func.date(EmailMessage.date) >= date_from)
    if date_to:
        statement = statement.where(func.date(EmailMessage.date) <= date_to)

    priority_rank = case(
        (EmailClassification.priority == "P0", 0),
        (EmailClassification.priority == "P1", 1),
        (EmailClassification.priority == "P2", 2),
        (EmailClassification.priority == "ignore", 3),
        else_=4,
    )
    statement = statement.order_by(None)
    if sort == "oldest":
        statement = statement.order_by(
            EmailMessage.date.asc().nullslast(), EmailMessage.id.asc()
        )
    elif sort == "priority":
        statement = statement.order_by(
            priority_rank.asc(), EmailMessage.date.desc().nullslast(), EmailMessage.id.desc()
        )
    else:
        statement = statement.order_by(
            EmailMessage.date.desc().nullslast(), EmailMessage.id.desc()
        )
    return statement


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
    priority: PriorityFilter | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    sort: SortOrder = "newest",
) -> dict:
    """Lista somente metadados e snippet; o corpo completo nunca vai para a fila."""
    if date_from and date_to and date_from > date_to:
        raise HTTPException(status_code=400, detail="A data inicial não pode vir após a final.")
    statement = _message_statement(
        page_size=page_size,
        mode=mode,
        query=query,
        account=account,
        category=category,
        priority=priority,
        date_from=date_from,
        date_to=date_to,
        sort=sort,
    )

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


@router.get("/selection")
def select_cleanup_suggestions(
    query: str | None = Query(None, max_length=300),
    account: str | None = Query(None, max_length=320),
    category: str | None = Query(None, max_length=40),
    priority: PriorityFilter | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    sort: SortOrder = "newest",
) -> dict:
    """Retorna até 200 IDs sugeridos pela IA dentro dos filtros atuais."""
    if date_from and date_to and date_from > date_to:
        raise HTTPException(status_code=400, detail="A data inicial não pode vir após a final.")
    statement = _message_statement(
        page_size=200,
        mode="candidates",
        query=query,
        account=account,
        category=category,
        priority=priority,
        date_from=date_from,
        date_to=date_to,
        sort=sort,
    )
    count_statement = select(func.count()).select_from(
        statement.order_by(None).limit(None).offset(None).subquery()
    )
    id_statement = statement.with_only_columns(
        EmailMessage.email_agent_id, maintain_column_froms=True
    )
    with db_session() as session:
        total = session.execute(count_statement).scalar_one()
        ids = list(session.execute(id_statement).scalars())
    return {"ids": ids, "total": total, "truncated": total > len(ids)}


@router.get("/messages/{email_agent_id}")
def get_cleanup_message(email_agent_id: str) -> dict:
    """Retorna o texto normalizado somente após seleção explícita na interface."""
    with db_session() as session:
        row = session.execute(
            select(EmailMessage, EmailAccount)
            .join(EmailAccount, EmailAccount.id == EmailMessage.account_id)
            .where(EmailMessage.email_agent_id == email_agent_id)
        ).one_or_none()
        if row is None:
            raise HTTPException(status_code=404, detail="Mensagem não encontrada.")
        msg, account = row
        classification = max(msg.classifications, key=lambda item: item.id) if msg.classifications else None
        return {
            **_candidate_payload(msg, classification, account),
            "body": msg.normalized_text or msg.snippet or "",
        }


@router.post("/messages/{email_agent_id}/blacklist")
def add_message_to_blacklist(email_agent_id: str, request: BlacklistRequest) -> dict:
    """Cria blacklist determinística; a ação automática continua sendo só uma label."""
    with db_session() as session:
        msg = session.execute(
            select(EmailMessage).where(EmailMessage.email_agent_id == email_agent_id)
        ).scalar_one_or_none()
        if msg is None:
            raise HTTPException(status_code=404, detail="Mensagem não encontrada.")
        sender = (msg.from_email or "").strip().lower()
        if "@" not in sender:
            raise HTTPException(status_code=400, detail="Remetente sem endereço válido.")
        value = sender if request.target == "sender" else sender.rsplit("@", 1)[1]
        slug = value.replace("@", "-at-").replace(".", "-")
        name = f"spam-{request.target}-{slug}"[:200]
        rule = session.execute(select(EmailRule).where(EmailRule.name == name)).scalar_one_or_none()
        if rule is None:
            rule = EmailRule(name=name, rule_type="spam", created_by="web")
            session.add(rule)
        rule.rule_type = "spam"
        rule.condition_json = {
            "scope": "*",
            "description": f"Blacklist criada pela interface para {value}.",
            "match": {request.target: value},
        }
        rule.action_json = {
            "priority": "ignore",
            "category": "spam_suspeito",
            "labels": ["AI/Spam Suspeito"],
        }
        rule.is_active = True
        classification = max(msg.classifications, key=lambda item: item.id) if msg.classifications else None
        if classification:
            classification.cleanup_candidate = True
            classification.cleanup_reason = f"Blacklist explícita de {request.target}: {value}."
    return {"status": "created", "target": request.target, "value": value}


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
