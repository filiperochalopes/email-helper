"""Sync Gmail via API oficial.

Incremental por users.history.list (startHistoryId). Importante: historyId
expira (HTTP 404) — nesse caso cai para um sync de janela por busca.
"""
import base64
from datetime import datetime, timedelta, timezone

from googleapiclient.errors import HttpError
from sqlalchemy import select

from email_agent.actions.gmail_actions import resolve_label_names
from email_agent.config import get_settings
from email_agent.connectors.gmail_client import get_service
from email_agent.logging_setup import get_logger
from email_agent.models import EmailAccount, EmailMessage, EmailUserEvent, MailboxCursor, db_session
from email_agent.parsing.mime_parser import parse_mime_bytes
from email_agent.sync.persist import persist_message

log = get_logger(__name__)

MONITORED_QUERY = "in:inbox OR in:spam OR in:sent"
CURSOR_MAILBOX = "_gmail_history"


def sync_account(account_id: int, bootstrap: bool = False) -> list[int]:
    with db_session() as session:
        account = session.get(EmailAccount, account_id)
    service = get_service(account)

    with db_session() as session:
        cursor = session.execute(
            select(MailboxCursor).where(
                MailboxCursor.account_id == account_id, MailboxCursor.mailbox == CURSOR_MAILBOX
            )
        ).scalar_one_or_none()
        last_history_id = cursor.last_history_id if cursor else None

    if last_history_id and not bootstrap:
        try:
            message_ids, new_history_id = _changed_ids_from_history(service, last_history_id)
        except HttpError as exc:
            if exc.resp.status == 404:  # historyId expirado -> janela de fallback
                log.warning("gmail_history_expired_fallback", account=account.email_address)
                message_ids, new_history_id = _ids_from_search(service, days=7)
            else:
                raise
    else:
        days = get_settings().default_sync_since_days if bootstrap else 7
        message_ids, new_history_id = _ids_from_search(service, days=days)

    new_db_ids = _fetch_and_persist(service, account, message_ids)

    with db_session() as session:
        cursor = session.execute(
            select(MailboxCursor).where(
                MailboxCursor.account_id == account_id, MailboxCursor.mailbox == CURSOR_MAILBOX
            )
        ).scalar_one_or_none()
        if cursor is None:
            cursor = MailboxCursor(account_id=account_id, mailbox=CURSOR_MAILBOX)
            session.add(cursor)
        if new_history_id:
            cursor.last_history_id = str(new_history_id)
        cursor.last_sync_at = datetime.now(timezone.utc)
        cursor.sync_status = "ok"
        db_account = session.get(EmailAccount, account_id)
        if db_account and db_account.auth_status != "ok":
            db_account.auth_status = "ok"

    log.info("gmail_synced", account=account.email_address, fetched=len(message_ids), new=len(new_db_ids))
    return new_db_ids


def _changed_ids_from_history(service, start_history_id: str) -> tuple[list[str], str | None]:
    ids: set[str] = set()
    new_history_id = None
    page_token = None
    while True:
        resp = (
            service.users()
            .history()
            .list(userId="me", startHistoryId=start_history_id, pageToken=page_token)
            .execute()
        )
        new_history_id = resp.get("historyId", new_history_id)
        for h in resp.get("history", []):
            for key in ("messagesAdded", "labelsAdded", "labelsRemoved"):
                for item in h.get(key, []):
                    ids.add(item["message"]["id"])
        page_token = resp.get("nextPageToken")
        if not page_token:
            break
    return list(ids), new_history_id


def _ids_from_search(service, days: int) -> tuple[list[str], str | None]:
    after = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y/%m/%d")
    query = f"({MONITORED_QUERY}) after:{after}"
    ids: list[str] = []
    page_token = None
    while True:
        resp = (
            service.users()
            .messages()
            .list(userId="me", q=query, maxResults=500, pageToken=page_token,
                  includeSpamTrash=True)
            .execute()
        )
        ids += [m["id"] for m in resp.get("messages", [])]
        page_token = resp.get("nextPageToken")
        if not page_token:
            break
    profile = service.users().getProfile(userId="me").execute()
    return ids, profile.get("historyId")


def _fetch_and_persist(service, account: EmailAccount, message_ids: list[str]) -> list[int]:
    new_db_ids: list[int] = []
    for mid in message_ids:
        try:
            full = service.users().messages().get(userId="me", id=mid, format="raw").execute()
        except HttpError as exc:
            log.warning("gmail_get_failed", id=mid, error=str(exc))
            continue
        raw = base64.urlsafe_b64decode(full["raw"])
        labels = full.get("labelIds", [])
        parsed = parse_mime_bytes(raw, max_chars=get_settings().max_email_text_chars)
        with db_session() as session:
            existing = session.execute(
                select(EmailMessage).where(
                    EmailMessage.account_id == account.id,
                    EmailMessage.provider_message_id == mid,
                )
            ).scalar_one_or_none()
            if existing:
                if set(existing.raw_labels or []) != set(labels):
                    # Grava o delta com NOMES de label (AI/Importante, ...) em vez de
                    # IDs do Gmail (Label_42), para a derivação de treino casar as
                    # labels AI. O event_type usa labels de sistema (INBOX/SPAM/...),
                    # que já são nomes, então é calculado com os IDs originais.
                    session.add(
                        EmailUserEvent(
                            message_id=existing.id,
                            event_type=_event_for_label_change(existing.raw_labels or [], labels),
                            previous_labels=resolve_label_names(
                                service, account.email_address, existing.raw_labels or []
                            ),
                            new_labels=resolve_label_names(
                                service, account.email_address, labels
                            ),
                            source="gmail_history",
                        )
                    )
                    existing.raw_labels = labels
                    existing.is_read = "UNREAD" not in labels
                continue
            msg, is_new = persist_message(
                session,
                account_id=account.id,
                provider="gmail_api",
                provider_message_id=mid,
                provider_thread_id=full.get("threadId"),
                mailbox=_mailbox_from_labels(labels),
                parsed=parsed,
                raw_labels=labels,
                is_sent_by_user="SENT" in labels,
                is_read="UNREAD" not in labels,
            )
            if is_new:
                new_db_ids.append(msg.id)
    return new_db_ids


def _mailbox_from_labels(labels: list[str]) -> str:
    for box in ("SPAM", "TRASH", "SENT", "INBOX"):
        if box in labels:
            return box
    return "ARCHIVE"


def _event_for_label_change(old: list[str], new: list[str]) -> str:
    old_s, new_s = set(old), set(new)
    if "TRASH" in new_s - old_s:
        return "moved_to_trash"
    if "SPAM" in old_s - new_s and "INBOX" in new_s:
        return "moved_from_spam_to_inbox"
    if "SPAM" in new_s - old_s:
        return "moved_to_spam"
    if "INBOX" in old_s - new_s and "TRASH" not in new_s:
        return "archived_by_user"
    return "label_changed"
