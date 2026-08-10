"""Exclusão confirmada pelo usuário (move para a Lixeira do provedor).

Diferente do safety_gate (pipeline automático, que NUNCA apaga), esta ação só é
disparada pelo comando CLI `delete`, com o usuário vendo o corpo e confirmando um
a um. Mesmo assim é não-destrutiva de fato: vai para a Lixeira (recuperável), passa
por idempotency_key e é registrada em email_action_log.
"""
from sqlalchemy import select

from email_agent.actions.idempotency import already_applied, log_action, make_idempotency_key
from email_agent.logging_setup import get_logger
from email_agent.models import EmailAccount, EmailMessage, db_session

log = get_logger(__name__)


def trash_message(email_agent_id: str) -> str:
    """Move para a Lixeira a mensagem com o id interno dado. Retorna um status:
    'trashed' | 'already' | 'error: ...'."""
    with db_session() as session:
        msg = session.execute(
            select(EmailMessage).where(EmailMessage.email_agent_id == email_agent_id)
        ).scalar_one_or_none()
        if msg is None:
            return "error: mensagem não encontrada"
        account = session.get(EmailAccount, msg.account_id)

        payload = {"action": "move_to_trash"}
        key = make_idempotency_key(
            msg.account_id, msg.provider_message_id, "move_to_trash", payload
        )
        if already_applied(session, key):
            return "already"

        try:
            if account.provider == "gmail_api":
                from email_agent.actions.gmail_actions import trash as gmail_trash

                gmail_trash(account, msg.provider_message_id)
                msg.raw_labels = [
                    label for label in (msg.raw_labels or []) if label != "INBOX"
                ]
                if "TRASH" not in msg.raw_labels:
                    msg.raw_labels.append("TRASH")
                msg.mailbox = "TRASH"
            else:
                from email_agent.actions.imap_actions import move_to_trash

                msg.mailbox = move_to_trash(account, msg)
            log_action(
                session, message_id=msg.id, action_type="move_to_trash",
                payload=payload, idempotency_key=key, status="success",
            )
            return "trashed"
        except Exception as exc:  # noqa: BLE001
            log.error("trash_failed", email=email_agent_id, error=str(exc))
            log_action(
                session, message_id=msg.id, action_type="move_to_trash",
                payload=payload, idempotency_key=key, status="error", error=str(exc),
            )
            return f"error: {exc}"
