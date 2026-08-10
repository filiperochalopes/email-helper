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
        _label_cache[account_email] = {
            label["name"]: label["id"] for label in resp.get("labels", [])
        }
    return _label_cache[account_email]


def resolve_label_names(service, account_email: str, label_ids: list[str]) -> list[str]:
    """Traduz IDs de label do Gmail (ex.: 'Label_42') para nomes legíveis
    (ex.: 'AI/Foco'). Labels de sistema (INBOX, SPAM, ...) têm id==nome e
    passam direto. Usado pelo sync para que os eventos de mudança de label gravem
    nomes nos eventos locais e na interface."""
    id_to_name = {lid: name for name, lid in _label_map(service, account_email).items()}
    return [id_to_name.get(lid, lid) for lid in label_ids]


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


def move_to_label(account: EmailAccount, provider_message_id: str, label_name: str) -> None:
    """Adiciona a label AI e REMOVE a label INBOX — equivalente a "Arquivar" do Gmail.
    O e-mail some da caixa de entrada mas continua acessível pela label (reversível)."""
    service = get_service(account)
    ensure_ai_labels(account)
    label_id = _label_map(service, account.email_address)[label_name]
    service.users().messages().modify(
        userId="me",
        id=provider_message_id,
        body={"addLabelIds": [label_id], "removeLabelIds": ["INBOX"]},
    ).execute()
    log.info("gmail_moved_to_label", account=account.email_address, label=label_name)


def remove_label(account: EmailAccount, provider_message_id: str, label_name: str) -> None:
    service = get_service(account)
    labels = _label_map(service, account.email_address)
    if label_name not in labels:
        return
    service.users().messages().modify(
        userId="me", id=provider_message_id, body={"removeLabelIds": [labels[label_name]]}
    ).execute()


def archive(account: EmailAccount, provider_message_id: str) -> None:
    """Arquiva nativamente no Gmail removendo somente a label de sistema INBOX."""
    service = get_service(account)
    service.users().messages().modify(
        userId="me", id=provider_message_id, body={"removeLabelIds": ["INBOX"]}
    ).execute()
    log.info("gmail_archived", account=account.email_address, msg=provider_message_id)


def trash(account: EmailAccount, provider_message_id: str) -> None:
    """Move a mensagem para a Lixeira do Gmail (recuperável ~30 dias).

    Só é chamado pelo comando CLI `delete`, com confirmação humana — nunca pelo
    pipeline automático."""
    service = get_service(account)
    service.users().messages().trash(userId="me", id=provider_message_id).execute()
    log.info("gmail_trashed", account=account.email_address, msg=provider_message_id)
