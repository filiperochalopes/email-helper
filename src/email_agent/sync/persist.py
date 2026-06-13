"""Persistência de mensagens normalizadas + deduplicação + eventos de mudança."""
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from email_agent.ids import generate_email_agent_id
from email_agent.models import EmailAttachment, EmailMessage, EmailUserEvent
from email_agent.parsing.mime_parser import ParsedEmail


def find_existing(
    session: Session, account_id: int, provider_message_id: str, mailbox: str
) -> EmailMessage | None:
    return session.execute(
        select(EmailMessage).where(
            EmailMessage.account_id == account_id,
            EmailMessage.provider_message_id == provider_message_id,
            EmailMessage.mailbox == mailbox,
        )
    ).scalar_one_or_none()


def find_by_header(session: Session, account_id: int, message_id_header: str | None) -> EmailMessage | None:
    if not message_id_header:
        return None
    return session.execute(
        select(EmailMessage)
        .where(
            EmailMessage.account_id == account_id,
            EmailMessage.message_id_header == message_id_header,
        )
        .limit(1)
    ).scalar_one_or_none()


def persist_message(
    session: Session,
    *,
    account_id: int,
    provider: str,
    provider_message_id: str,
    provider_thread_id: str | None,
    mailbox: str,
    parsed: ParsedEmail,
    raw_labels: list[str] | None,
    is_sent_by_user: bool,
    is_read: bool = False,
    role: str | None = None,
) -> tuple[EmailMessage, bool]:
    """Insere ou atualiza. Retorna (mensagem, is_new).

    Dedup: mesma conta + Message-ID header já visto em outra pasta => é a mesma
    mensagem movida; gera evento de mudança (moved_to_spam / moved_from_spam_to_inbox /
    moved_to_trash / moved_to_folder) em vez de duplicar.
    """
    existing = find_existing(session, account_id, provider_message_id, mailbox)
    if existing:
        if raw_labels is not None and set(existing.raw_labels or []) != set(raw_labels):
            session.add(
                EmailUserEvent(
                    message_id=existing.id,
                    event_type="label_changed",
                    previous_labels=existing.raw_labels,
                    new_labels=raw_labels,
                    source="sync_diff",
                )
            )
            existing.raw_labels = raw_labels
        return existing, False

    moved = find_by_header(session, account_id, parsed.message_id_header)
    if moved is not None:
        prev_role = (moved.raw_labels or [None])[0]
        event_type = "moved_to_folder"
        if role == "spam":
            event_type = "moved_to_spam"
        elif role == "trash":
            event_type = "moved_to_trash"
        elif role == "inbox" and prev_role == "SPAM":
            event_type = "moved_from_spam_to_inbox"
        session.add(
            EmailUserEvent(
                message_id=moved.id,
                event_type=event_type,
                previous_mailbox=moved.mailbox,
                new_mailbox=mailbox,
                source="sync_diff",
            )
        )
        moved.mailbox = mailbox
        moved.provider_message_id = provider_message_id
        if raw_labels is not None:
            moved.raw_labels = raw_labels
        return moved, False

    msg = EmailMessage(
        email_agent_id=generate_email_agent_id(session, datetime.now(timezone.utc)),
        account_id=account_id,
        provider_message_id=provider_message_id,
        provider_thread_id=provider_thread_id,
        message_id_header=parsed.message_id_header,
        mailbox=mailbox,
        from_email=parsed.from_email,
        from_name=parsed.from_name,
        to_json=parsed.to,
        cc_json=parsed.cc,
        subject=parsed.subject,
        date=parsed.date,
        received_at=datetime.now(timezone.utc),
        snippet=parsed.normalized_text[:300],
        normalized_text=parsed.normalized_text,
        normalized_text_hash=parsed.normalized_text_hash,
        has_attachment=parsed.has_attachment,
        is_read=is_read,
        is_sent_by_user=is_sent_by_user,
        raw_labels=raw_labels,
        ai_labels=[],
    )
    session.add(msg)
    session.flush()
    for att in parsed.attachments:
        session.add(
            EmailAttachment(
                message_id=msg.id,
                filename=att.filename,
                content_type=att.content_type,
                size_bytes=att.size_bytes,
                sha256=att.sha256,
            )
        )
    return msg, True
