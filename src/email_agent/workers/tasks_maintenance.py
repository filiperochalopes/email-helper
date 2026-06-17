from datetime import datetime, timedelta, timezone

from sqlalchemy import update

from email_agent.intelligence.ollama_client import flush_langfuse
from email_agent.intelligence.training import derive_training_from_user_events, fit_models
from email_agent.labelstudio.sync import pull_annotations, push_pending_tasks
from email_agent.logging_setup import get_logger
from email_agent.models import HumanReview, db_session
from email_agent.workers.celery_app import app

log = get_logger(__name__)

REVIEW_EXPIRY_DAYS = 30


@app.task(name="email_agent.nightly_maintenance")
def nightly_maintenance() -> dict:
    # Label Studio (polling): puxa anotações novas antes do treino, depois
    # empurra os pendentes do dia. Falha aqui não derruba a manutenção.
    pulled = pushed = 0
    try:
        pulled = pull_annotations()
    except Exception as exc:  # noqa: BLE001
        log.warning("labelstudio_pull_error", error=str(exc))

    derived = derive_training_from_user_events()
    fit = fit_models()
    trained = fit["spam_samples"]

    try:
        pushed = push_pending_tasks()
    except Exception as exc:  # noqa: BLE001
        log.warning("labelstudio_push_error", error=str(exc))

    with db_session() as session:
        expired = session.execute(
            update(HumanReview)
            .where(
                HumanReview.status == "pending",
                HumanReview.created_at < datetime.now(timezone.utc) - timedelta(days=REVIEW_EXPIRY_DAYS),
            )
            .values(status="expired")
        ).rowcount

    flush_langfuse()
    log.info(
        "maintenance_done",
        training_derived=derived,
        trained_spam=trained,
        trained_category=fit["category_samples"],
        expired=expired,
        labelstudio_pulled=pulled,
        labelstudio_pushed=pushed,
    )
    return {
        "training_derived": derived,
        "trained_spam_samples": trained,
        "trained_category_samples": fit["category_samples"],
        "expired_reviews": expired,
        "labelstudio_pulled": pulled,
        "labelstudio_pushed": pushed,
    }
