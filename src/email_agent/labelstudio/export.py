"""Exporta mensagens para revisão em lote no Label Studio (formato JSON de tasks)."""
import json
from pathlib import Path

from sqlalchemy import select

from email_agent.models import EmailClassification, EmailMessage, HumanReview, db_session


def export_tasks(
    output_file: str,
    *,
    ai_label: str | None = None,
    uncertain: bool = False,
    limit: int = 500,
) -> int:
    with db_session() as session:
        query = (
            select(EmailMessage, EmailClassification)
            .join(EmailClassification, EmailClassification.message_id == EmailMessage.id)
            .order_by(EmailMessage.id.desc())
            .limit(limit)
        )
        rows = session.execute(query).all()

        tasks = []
        for msg, cls in rows:
            if ai_label and ai_label not in (msg.ai_labels or []):
                continue
            if uncertain and (cls.confidence or 0) >= 0.6:
                continue
            tasks.append(
                {
                    "data": {
                        "email_agent_id": msg.email_agent_id,
                        "account_id": msg.account_id,
                        "mailbox": msg.mailbox,
                        "from": f"{msg.from_name or ''} <{msg.from_email or ''}>",
                        "subject": msg.subject,
                        "date": msg.date.isoformat() if msg.date else None,
                        "snippet": msg.snippet,
                        "text": (msg.normalized_text or "")[:8000],
                        "attachments": [a.filename for a in msg.attachments],
                        "suggested_category": cls.category,
                        "spam_score": cls.spam_score,
                        "importance_score": cls.importance_score,
                        "reason": cls.importance_reason,
                        "current_ai_labels": msg.ai_labels,
                    }
                }
            )
            session.add(
                HumanReview(
                    message_id=msg.id,
                    review_type="labelstudio_export",
                    status="exported_labelstudio",
                )
            )

    Path(output_file).parent.mkdir(parents=True, exist_ok=True)
    with open(output_file, "w") as f:
        json.dump(tasks, f, ensure_ascii=False, indent=2)
    return len(tasks)
