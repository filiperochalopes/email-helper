"""Importa anotações do Label Studio (export JSON) como eventos de treino confiáveis."""
import json

from sqlalchemy import select

from email_agent.intelligence.taxonomy import CATEGORIES
from email_agent.logging_setup import get_logger
from email_agent.models import EmailMessage, EmailTrainingEvent, db_session

log = get_logger(__name__)


def import_annotations(file_path: str) -> int:
    with open(file_path) as f:
        items = json.load(f)

    created = 0
    with db_session() as session:
        for item in items:
            data = item.get("data", {})
            email_agent_id = data.get("email_agent_id")
            label = _extract_choice(item)
            if not email_agent_id or label not in CATEGORIES:
                continue
            msg = session.execute(
                select(EmailMessage).where(EmailMessage.email_agent_id == email_agent_id)
            ).scalar_one_or_none()
            if msg is None:
                continue
            session.add(
                EmailTrainingEvent(
                    message_id=msg.id,
                    label=label,
                    source="label_studio",
                    weight=1.0,
                    trusted=True,
                    reason="anotação Label Studio",
                )
            )
            created += 1
    log.info("labelstudio_imported", created=created)
    return created


def _extract_choice(item: dict) -> str | None:
    for ann in item.get("annotations", []):
        for res in ann.get("result", []):
            choices = res.get("value", {}).get("choices", [])
            if choices:
                return choices[0]
    return None
