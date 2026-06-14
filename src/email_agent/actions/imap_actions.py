"""Ações IMAP: como IMAP não tem labels, a "label AI" vira CÓPIA da mensagem
para a pasta AI correspondente (não destrutivo: o original fica onde está)."""
from email_agent.connectors.imap_client import connect, discover_folders, ensure_ai_folders
from email_agent.intelligence.taxonomy import ALL_AI_LABELS
from email_agent.logging_setup import get_logger
from email_agent.models import EmailAccount, EmailMessage

log = get_logger(__name__)


def copy_to_ai_folder(account: EmailAccount, msg: EmailMessage, label: str) -> None:
    with connect(account) as client:
        mapping = ensure_ai_folders(client, ALL_AI_LABELS)
        folder = mapping[label]
        client.select_folder(msg.mailbox, readonly=False)
        uid = int(msg.provider_message_id.split(":")[-1])
        client.copy([uid], folder)
        log.info("imap_copied_to_ai_folder", account=account.email_address, folder=folder, uid=uid)


def move_to_trash(account: EmailAccount, msg: EmailMessage) -> None:
    """Move a mensagem para a pasta Trash do servidor (recuperável).

    Só é chamado pelo comando CLI `delete`, com confirmação humana — nunca pelo
    pipeline automático. Usa MOVE quando disponível; senão copy + \\Deleted +
    expunge restrito ao UID."""
    with connect(account) as client:
        trash = discover_folders(client).get("trash")
        if not trash:
            raise RuntimeError(f"Pasta Trash não encontrada para {account.email_address}")
        client.select_folder(msg.mailbox, readonly=False)
        uid = int(msg.provider_message_id.split(":")[-1])
        if client.has_capability("MOVE"):
            client.move([uid], trash)
        else:
            client.copy([uid], trash)
            client.delete_messages([uid])
            client.expunge([uid])
        log.info("imap_trashed", account=account.email_address, folder=trash, uid=uid)
