"""Ações IMAP para labels, Arquivo nativo e Lixeira recuperável.

Como IMAP não tem labels, ``AI/Spam Suspeito`` move a mensagem para uma pasta;
``AI/Foco`` permanece na Inbox como keyword quando o servidor permitir.
"""
from email_agent.connectors.imap_client import (
    connect,
    discover_folders,
    ensure_ai_folders,
    ensure_archive_folder,
)
from email_agent.intelligence.taxonomy import ALL_AI_LABELS, imap_keyword
from email_agent.logging_setup import get_logger
from email_agent.models import EmailAccount, EmailMessage

log = get_logger(__name__)


def add_keyword(account: EmailAccount, msg: EmailMessage, label: str) -> bool:
    """Aplica a label como KEYWORD IMAP (etiqueta no lugar, não move). Usado para as
    labels que ficam na INBOX (`AI/Foco`). Best-effort: se o servidor não
    permitir keywords personalizadas (sem `\\*` em PERMANENTFLAGS), não faz nada e
    retorna False — o chamador registra como 'skipped'."""
    keyword = imap_keyword(label)
    with connect(account) as client:
        info = client.select_folder(msg.mailbox, readonly=False)
        perm = info.get(b"PERMANENTFLAGS", ()) or ()
        if b"\\*" not in perm:
            log.info("imap_keywords_unsupported", account=account.email_address,
                     folder=msg.mailbox, keyword=keyword)
            return False
        uid = int(msg.provider_message_id.split(":")[-1])
        client.add_flags([uid], [keyword])
        log.info("imap_keyword_added", account=account.email_address,
                 folder=msg.mailbox, uid=uid, keyword=keyword)
        return True


def _move_uid(client, source_folder: str, uid: int, dest_folder: str) -> None:
    """Move um UID de source para dest (recuperável: nunca apaga sem copiar antes).
    Usa MOVE quando disponível; senão copy + \\Deleted + expunge restrito ao UID."""
    client.select_folder(source_folder, readonly=False)
    if client.has_capability("MOVE"):
        client.move([uid], dest_folder)
    else:
        client.copy([uid], dest_folder)
        client.delete_messages([uid])
        client.expunge([uid])


def move_to_ai_folder(account: EmailAccount, msg: EmailMessage, label: str) -> str:
    """Move a mensagem da pasta atual para a pasta AI da label. Retorna a pasta destino.

    O original sai da INBOX, sem duplicação."""
    with connect(account) as client:
        mapping = ensure_ai_folders(client, ALL_AI_LABELS)
        dest = mapping[label]
        uid = int(msg.provider_message_id.split(":")[-1])
        _move_uid(client, msg.mailbox, uid, dest)
        log.info("imap_moved_to_ai_folder", account=account.email_address, folder=dest, uid=uid)
    return dest


def move_to_trash(account: EmailAccount, msg: EmailMessage) -> str:
    """Move a mensagem para a pasta Trash do servidor (recuperável).

    Só é chamado pelo comando CLI `delete`, com confirmação humana — nunca pelo
    pipeline automático."""
    with connect(account) as client:
        trash = discover_folders(client).get("trash")
        if not trash:
            raise RuntimeError(f"Pasta Trash não encontrada para {account.email_address}")
        uid = int(msg.provider_message_id.split(":")[-1])
        _move_uid(client, msg.mailbox, uid, trash)
        log.info("imap_trashed", account=account.email_address, folder=trash, uid=uid)
        return trash


def move_to_archive(account: EmailAccount, msg: EmailMessage) -> str:
    """Move para a pasta Archive anunciada pelo servidor/Canary.

    Se nenhuma pasta tiver SPECIAL-USE ``\\Archive`` nem nome reconhecido, cria
    ``Archive`` uma única vez. Retorna o nome real usado pelo servidor.
    """
    with connect(account) as client:
        archive = ensure_archive_folder(client)
        uid = int(msg.provider_message_id.split(":")[-1])
        _move_uid(client, msg.mailbox, uid, archive)
        log.info("imap_archived", account=account.email_address, folder=archive, uid=uid)
        return archive
