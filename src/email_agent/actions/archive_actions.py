"""Arquivamento nativo: tira o e-mail da INBOX e o guarda na pasta Archive do provedor.

O nome IMAP é descoberto por ``\\Archive`` (ou nomes conhecidos), o que mantém
compatibilidade com o Canary. A ação é recuperável, idempotente e nunca deleta.
"""
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from email_agent.actions.idempotency import already_applied, log_action, make_idempotency_key
from email_agent.logging_setup import get_logger
from email_agent.models import EmailAccount, EmailMessage, db_session

log = get_logger(__name__)

def _in_inbox(msg: EmailMessage) -> bool:
    """True se o e-mail ainda está na caixa de entrada (não foi movido/arquivado)."""
    return (msg.mailbox or "").upper() == "INBOX" or "INBOX" in (msg.raw_labels or [])


def _archive_in_session(session: Session, msg: EmailMessage) -> str:
    account = session.get(EmailAccount, msg.account_id)
    payload = {"action": "move_to_archive"}
    key = make_idempotency_key(msg.account_id, msg.provider_message_id, "move_to_archive", payload)
    if already_applied(session, key):
        return "already"
    try:
        if account.provider == "gmail_api":
            from email_agent.actions.gmail_actions import archive

            archive(account, msg.provider_message_id)
            msg.raw_labels = [label for label in (msg.raw_labels or []) if label != "INBOX"]
        else:
            from email_agent.actions.imap_actions import move_to_archive

            msg.mailbox = move_to_archive(account, msg)
        log_action(session, message_id=msg.id, action_type="move_to_archive",
                   payload=payload, idempotency_key=key, status="success")
        return "archived"
    except Exception as exc:  # noqa: BLE001 — uma conta/e-mail falhar não derruba os demais
        log.error("archive_failed", email=msg.email_agent_id, error=str(exc))
        log_action(session, message_id=msg.id, action_type="move_to_archive",
                   payload=payload, idempotency_key=key, status="error", error=str(exc))
        return f"error: {exc}"


def archive_message(email_agent_id: str) -> str:
    """Arquiva uma mensagem pelo id interno. Retorna 'archived'|'already'|'error: ...'."""
    with db_session() as session:
        msg = session.execute(
            select(EmailMessage).where(EmailMessage.email_agent_id == email_agent_id)
        ).scalar_one_or_none()
        if msg is None:
            return "error: mensagem não encontrada"
        return _archive_in_session(session, msg)


def find_manual_archive_candidates(before: datetime, account_email: str | None = None) -> list[str]:
    """E-mails ainda na INBOX e anteriores ao cutoff para revisão/arquivo explícito."""
    with db_session() as session:
        q = (
            select(EmailMessage)
            .where(EmailMessage.date < before)
            .order_by(EmailMessage.date.asc())
        )
        if account_email:
            acc = session.execute(
                select(EmailAccount).where(EmailAccount.email_address == account_email)
            ).scalar_one_or_none()
            if acc is None:
                return []
            q = q.where(EmailMessage.account_id == acc.id)
        rows = session.execute(q).scalars().all()
        out = []
        for msg in rows:
            if not _in_inbox(msg):
                continue
            out.append(msg.email_agent_id)
        return out
