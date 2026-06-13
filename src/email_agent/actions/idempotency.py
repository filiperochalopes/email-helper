import hashlib
import json

from sqlalchemy import select
from sqlalchemy.orm import Session

from email_agent.models import EmailActionLog


def make_idempotency_key(account_id: int, provider_message_id: str, action_type: str, payload: dict) -> str:
    basis = f"{account_id}|{provider_message_id}|{action_type}|{json.dumps(payload, sort_keys=True)}"
    return hashlib.sha256(basis.encode()).hexdigest()


def already_applied(session: Session, idempotency_key: str) -> bool:
    row = session.execute(
        select(EmailActionLog).where(
            EmailActionLog.idempotency_key == idempotency_key,
            EmailActionLog.status == "success",
        )
    ).first()
    return row is not None


def log_action(
    session: Session,
    *,
    message_id: int,
    action_type: str,
    payload: dict,
    idempotency_key: str,
    status: str,
    error: str | None = None,
) -> None:
    session.add(
        EmailActionLog(
            message_id=message_id,
            action_type=action_type,
            action_payload=payload,
            status=status,
            error=error,
            idempotency_key=idempotency_key,
        )
    )
