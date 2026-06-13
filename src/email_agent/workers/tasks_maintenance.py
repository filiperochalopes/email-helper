from datetime import datetime, timedelta, timezone

from sqlalchemy import update

from email_agent.intelligence.training import derive_training_from_user_events, fit_spam_model
from email_agent.logging_setup import get_logger
from email_agent.models import HumanReview, db_session
from email_agent.workers.celery_app import app

log = get_logger(__name__)

REVIEW_EXPIRY_DAYS = 30


@app.task(name="email_agent.nightly_maintenance")
def nightly_maintenance() -> dict:
    derived = derive_training_from_user_events()
    trained = fit_spam_model()

    with db_session() as session:
        expired = session.execute(
            update(HumanReview)
            .where(
                HumanReview.status == "pending",
                HumanReview.created_at < datetime.now(timezone.utc) - timedelta(days=REVIEW_EXPIRY_DAYS),
            )
            .values(status="expired")
        ).rowcount

    log.info("maintenance_done", training_derived=derived, trained_samples=trained, expired=expired)
    return {"training_derived": derived, "trained_samples": trained, "expired_reviews": expired}
