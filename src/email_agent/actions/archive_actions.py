"""Arquivamento em AI/Archive: tira o e-mail da INBOX e o guarda como arquivo morto
(recuperável — nunca deleta). Disparado por:
- comando CLI/TUI `archive` com cutoff de data (decisão do usuário);
- ciclo diário automático estrito (Importante/Documento já lido com >6 meses).
Passa por idempotency_key e registra em email_action_log."""
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from email_agent.actions.idempotency import already_applied, log_action, make_idempotency_key
from email_agent.intelligence.taxonomy import (
    LABEL_ARCHIVE,
    LABEL_DOCUMENTOS,
    LABEL_FISCAL,
    LABEL_IMPORTANTE,
    LABEL_MARKETING,
    LABEL_SPAM_SUSPEITO,
)
from email_agent.logging_setup import get_logger
from email_agent.models import EmailAccount, EmailMessage, db_session

log = get_logger(__name__)

# Auto-archive estrito: só estas labels (família Importante/Documento) entram no ciclo.
AUTO_ARCHIVE_LABELS = {LABEL_IMPORTANTE, LABEL_DOCUMENTOS, LABEL_FISCAL}
# Cutoff manual: NÃO arquivar o que é ruído (já tem pasta própria) nem spam.
MANUAL_ARCHIVE_EXCLUDE = {LABEL_MARKETING, LABEL_SPAM_SUSPEITO}


def _in_inbox(msg: EmailMessage) -> bool:
    """True se o e-mail ainda está na caixa de entrada (não foi movido/arquivado)."""
    if LABEL_ARCHIVE in (msg.ai_labels or []):
        return False
    return (msg.mailbox or "").upper() == "INBOX" or "INBOX" in (msg.raw_labels or [])


def _archive_in_session(session: Session, msg: EmailMessage) -> str:
    account = session.get(EmailAccount, msg.account_id)
    payload = {"action": "move_to_archive"}
    key = make_idempotency_key(msg.account_id, msg.provider_message_id, "move_to_archive", payload)
    if already_applied(session, key):
        return "already"
    try:
        if account.provider == "gmail_api":
            from email_agent.actions.gmail_actions import move_to_label

            move_to_label(account, msg.provider_message_id, LABEL_ARCHIVE)
            msg.raw_labels = [l for l in (msg.raw_labels or []) if l != "INBOX"]
        else:
            from email_agent.actions.imap_actions import move_to_ai_folder

            msg.mailbox = move_to_ai_folder(account, msg, LABEL_ARCHIVE)
        msg.ai_labels = sorted(set(msg.ai_labels or []) | {LABEL_ARCHIVE})
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
    """Candidatos do fluxo manual: e-mails na INBOX, anteriores ao cutoff, que NÃO são
    marketing/notícia nem spam. Retorna ids internos (E-...) para o usuário confirmar."""
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
            if MANUAL_ARCHIVE_EXCLUDE & set(msg.ai_labels or []):
                continue
            out.append(msg.email_agent_id)
        return out


def auto_archive_old(min_age_days: int = 180) -> dict:
    """Ciclo diário ESTRITO: arquiva e-mails Importante/Documento, JÁ LIDOS, com mais
    de `min_age_days` (padrão 6 meses), que ainda estão na INBOX. Nada além disso."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=min_age_days)
    archived = skipped = errors = 0
    with db_session() as session:
        # Filtro grosso no banco (lidos + antigos); a checagem de label/inbox é em
        # Python — ai_labels é JSON, sem operador de containment portável.
        rows = session.execute(
            select(EmailMessage)
            .where(EmailMessage.is_read.is_(True))
            .where(EmailMessage.date < cutoff)
        ).scalars().all()
        for msg in rows:
            if not (AUTO_ARCHIVE_LABELS & set(msg.ai_labels or [])):
                continue
            if not _in_inbox(msg):
                skipped += 1
                continue
            status = _archive_in_session(session, msg)
            if status == "archived":
                archived += 1
            elif status == "already":
                skipped += 1
            else:
                errors += 1
    log.info("auto_archive_done", archived=archived, skipped=skipped, errors=errors)
    return {"archived": archived, "skipped": skipped, "errors": errors}
