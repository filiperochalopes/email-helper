from sqlalchemy import select

from email_agent.actions.safety_gate import plan_safe_actions
from email_agent.intelligence.graph import run_pipeline
from email_agent.logging_setup import get_logger
from email_agent.models import EmailClassification, EmailMessage, db_session
from email_agent.workers.celery_app import app

log = get_logger(__name__)


_SKIP_ROLES = {"SPAM", "TRASH"}


@app.task(name="email_agent.classify_message", max_retries=2, autoretry_for=(Exception,))
def classify_message(db_message_id: int) -> dict:
    with db_session() as session:
        msg = session.get(EmailMessage, db_message_id)
        if msg and _SKIP_ROLES.intersection(msg.raw_labels or []):
            log.info("classify_skipped", role=(msg.raw_labels or []), email_id=db_message_id)
            return {"skipped": "spam_or_trash"}
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


@app.task(name="email_agent.reapply_organization", max_retries=2, autoretry_for=(Exception,))
def reapply_organization(db_message_id: int) -> dict:
    """Migração: reaplica a organização (mover/label) no provedor a partir das labels
    que o e-mail JÁ tem — sem reclassificar (não chama LLM). Idempotente via safety_gate."""
    with db_session() as session:
        msg = session.get(EmailMessage, db_message_id)
        if msg is None:
            return {"skipped": "not_found"}
        labels = list(msg.ai_labels or [])
    result = plan_safe_actions({"db_message_id": db_message_id, "suggested_labels": labels, "errors": []})
    return {"applied": result.get("applied_actions", [])}


@app.task(name="email_agent.reapply_pending")
def reapply_pending(limit: int = 100000) -> dict:
    """Enfileira reaplicação de organização para e-mails ainda na INBOX que têm QUALQUER
    label AI — migra o comportamento antigo (copiar): as labels de "sair" movem para a
    pasta; as que ficam (Importante/Aguardando) ganham keyword IMAP."""
    with db_session() as session:
        rows = (
            session.execute(
                select(EmailMessage.id, EmailMessage.ai_labels)
                .where(EmailMessage.mailbox == "INBOX")
                .order_by(EmailMessage.id.desc())
                .limit(limit)
            )
            .all()
        )
    targets = [mid for mid, ai in rows if ai]
    for mid in targets:
        reapply_organization.delay(mid)
    log.info("reapply_pending_enqueued", count=len(targets), scanned=len(rows))
    return {"enqueued": len(targets), "scanned_inbox": len(rows)}
