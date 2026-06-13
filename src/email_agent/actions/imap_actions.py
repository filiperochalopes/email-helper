"""Ações IMAP: como IMAP não tem labels, a "label AI" vira CÓPIA da mensagem
para a pasta AI correspondente (não destrutivo: o original fica onde está)."""
from email_agent.connectors.imap_client import connect, ensure_ai_folders
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
