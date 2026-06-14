"""Ações Gmail: labels AI. Nunca deleta, nunca move para Trash no MVP."""
from email_agent.connectors.gmail_client import get_service
from email_agent.intelligence.taxonomy import ALL_AI_LABELS
from email_agent.logging_setup import get_logger
from email_agent.models import EmailAccount

log = get_logger(__name__)

_label_cache: dict[str, dict[str, str]] = {}  # email -> {label_name: label_id}


def _label_map(service, account_email: str) -> dict[str, str]:
    if account_email not in _label_cache:
        resp = service.users().labels().list(userId="me").execute()
        _label_cache[account_email] = {l["name"]: l["id"] for l in resp.get("labels", [])}
    return _label_cache[account_email]


def ensure_ai_labels(account: EmailAccount) -> None:
    service = get_service(account)
    existing = _label_map(service, account.email_address)
    for name in ALL_AI_LABELS:
        if name not in existing:
            created = (
                service.users()
                .labels()
                .create(
                    userId="me",
                    body={
                        "name": name,
                        "labelListVisibility": "labelShow",
                        "messageListVisibility": "show",
                    },
                )
                .execute()
            )
            existing[name] = created["id"]
            log.info("gmail_label_created", account=account.email_address, label=name)


def add_label(account: EmailAccount, provider_message_id: str, label_name: str) -> None:
    service = get_service(account)
    ensure_ai_labels(account)
    label_id = _label_map(service, account.email_address)[label_name]
    service.users().messages().modify(
        userId="me", id=provider_message_id, body={"addLabelIds": [label_id]}
    ).execute()


def remove_label(account: EmailAccount, provider_message_id: str, label_name: str) -> None:
    service = get_service(account)
    labels = _label_map(service, account.email_address)
    if label_name not in labels:
        return
    service.users().messages().modify(
        userId="me", id=provider_message_id, body={"removeLabelIds": [labels[label_name]]}
    ).execute()


def trash(account: EmailAccount, provider_message_id: str) -> None:
    """Move a mensagem para a Lixeira do Gmail (recuperável ~30 dias).

    Só é chamado pelo comando CLI `delete`, com confirmação humana — nunca pelo
    pipeline automático."""
    service = get_service(account)
    service.users().messages().trash(userId="me", id=provider_message_id).execute()
    log.info("gmail_trashed", account=account.email_address, msg=provider_message_id)
