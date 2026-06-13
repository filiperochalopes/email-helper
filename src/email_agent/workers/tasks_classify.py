from sqlalchemy import select

from email_agent.intelligence.graph import run_pipeline
from email_agent.logging_setup import get_logger
from email_agent.models import EmailClassification, EmailMessage, db_session
from email_agent.workers.celery_app import app

log = get_logger(__name__)


@app.task(name="email_agent.classify_message", max_retries=2, autoretry_for=(Exception,))
def classify_message(db_message_id: int) -> dict:
    state = run_pipeline(db_message_id)
    return {
        "email_agent_id": state.get("email_agent_id"),
        "category": state.get("category"),
        "priority": state.get("priority"),
        "labels": state.get("suggested_labels"),
    }


@app.task(name="email_agent.classify_pending")
def classify_pending(limit: int = 2000) -> dict:
    """Classifica mensagens que ainda não têm classificação."""
    with db_session() as session:
        classified = select(EmailClassification.message_id)
        pending = (
            session.execute(
                select(EmailMessage.id)
                .where(EmailMessage.id.not_in(classified))
                .order_by(EmailMessage.id.desc())
                .limit(limit)
            )
            .scalars()
            .all()
        )
    for db_id in pending:
        classify_message.delay(db_id)
    log.info("classify_pending_enqueued", count=len(pending))
    return {"enqueued": len(pending)}
