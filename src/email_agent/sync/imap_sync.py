"""Sync incremental IMAP por UID, respeitando UIDVALIDITY, com BODY.PEEK."""
from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from email_agent.config import get_settings
from email_agent.connectors.imap_client import connect, discover_folders
from email_agent.logging_setup import get_logger
from email_agent.models import EmailAccount, MailboxCursor, db_session
from email_agent.parsing.mime_parser import parse_mime_bytes
from email_agent.sync.persist import persist_message

log = get_logger(__name__)


def _get_cursor(session, account_id: int, mailbox: str) -> MailboxCursor:
    cursor = session.execute(
        select(MailboxCursor).where(
            MailboxCursor.account_id == account_id, MailboxCursor.mailbox == mailbox
        )
    ).scalar_one_or_none()
    if cursor is None:
        cursor = MailboxCursor(account_id=account_id, mailbox=mailbox)
        session.add(cursor)
        session.flush()
    return cursor


def sync_account(account_id: int, bootstrap: bool = False) -> list[int]:
    """Sincroniza uma conta IMAP. Retorna ids (banco) apenas das mensagens novas
    da INBOX e Sent — spam/trash são persistidos para dedup mas não classificados
    (evita que o agente aplique labels AI em emails já descartados pelo usuário)."""
    settings = get_settings()
    classify_ids: list[int] = []
    with db_session() as session:
        account = session.get(EmailAccount, account_id)

    try:
        client = connect(account)
    except Exception as exc:  # noqa: BLE001 — falha numa conta não pode derrubar as outras
        log.error("imap_connect_failed", account=account.email_address, error=str(exc))
        with db_session() as session:
            db_acc = session.get(EmailAccount, account_id)
            db_acc.auth_status = "error"
        return []

    with client:
        folders = discover_folders(client)
        # (role, folder): spam pode ter várias pastas (Junk, spam, INBOX.spam...)
        monitored: list[tuple[str, str]] = [("inbox", folders["inbox"])]
        for spam_folder in folders["spam"]:  # type: ignore[union-attr]
            monitored.append(("spam", spam_folder))
        if folders["sent"]:
            monitored.append(("sent", folders["sent"]))  # type: ignore[arg-type]
        if folders["trash"]:
            monitored.append(("trash", folders["trash"]))  # type: ignore[arg-type]
        for role, folder in monitored:
            try:
                new_ids = _sync_folder(client, account, role, folder, bootstrap, settings)
                if role in ("inbox", "sent"):
                    classify_ids += new_ids
                # spam/trash: persistidos para dedup, não enfileirados para classificação
            except Exception as exc:  # noqa: BLE001
                log.error(
                    "imap_folder_sync_failed",
                    account=account.email_address, folder=folder, error=str(exc),
                )
    return classify_ids


def _sync_folder(client, account, role: str, folder: str, bootstrap: bool, settings) -> list[int]:
    new_ids: list[int] = []
    info = client.select_folder(folder, readonly=True)
    uidvalidity = info.get(b"UIDVALIDITY")

    with db_session() as session:
        cursor = _get_cursor(session, account.id, folder)
        if cursor.uidvalidity is not None and cursor.uidvalidity != uidvalidity:
            log.warning("uidvalidity_changed", account=account.email_address, folder=folder)
            cursor.last_uid = None  # ressincronizar; dedup por Message-ID evita duplicatas
        cursor.uidvalidity = uidvalidity
        last_uid = cursor.last_uid

    if last_uid:
        uids = client.search(["UID", f"{last_uid + 1}:*"])
        uids = [u for u in uids if u > last_uid]
    else:
        since = datetime.now(UTC) - timedelta(days=settings.default_sync_since_days)
        uids = client.search(["SINCE", since.date()])

    if not uids:
        with db_session() as session:
            cursor = _get_cursor(session, account.id, folder)
            if cursor.last_uid is None:
                uidnext = info.get(b"UIDNEXT")
                cursor.last_uid = (uidnext - 1) if uidnext else 0
            cursor.last_sync_at = datetime.now(UTC)
            cursor.sync_status = "ok"
        return []

    for batch_start in range(0, len(uids), 50):
        batch = uids[batch_start : batch_start + 50]
        response = client.fetch(batch, ["BODY.PEEK[]", "FLAGS"])
        with db_session() as session:
            for uid, data in response.items():
                raw = data.get(b"BODY[]")
                if not raw:
                    continue
                parsed = parse_mime_bytes(raw, max_chars=settings.max_email_text_chars)
                flags = [f.decode() if isinstance(f, bytes) else str(f) for f in data.get(b"FLAGS", [])]
                msg, is_new = persist_message(
                    session,
                    account_id=account.id,
                    provider="imap",
                    provider_message_id=f"{folder}:{uidvalidity}:{uid}",
                    provider_thread_id=None,
                    mailbox=folder,
                    parsed=parsed,
                    raw_labels=[role.upper()],
                    is_sent_by_user=(role == "sent"),
                    is_read="\\Seen" in flags,
                    role=role,
                )
                if is_new:
                    new_ids.append(msg.id)

    with db_session() as session:
        cursor = _get_cursor(session, account.id, folder)
        cursor.last_uid = max(uids)
        cursor.last_sync_at = datetime.now(UTC)
        cursor.sync_status = "ok"

    log.info("imap_folder_synced", account=account.email_address, folder=folder, new=len(new_ids))
    return new_ids
