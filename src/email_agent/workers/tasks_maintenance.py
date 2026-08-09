from datetime import datetime, timedelta, timezone

from sqlalchemy import update

from email_agent.config import get_settings
from email_agent.intelligence.ollama_client import flush_langfuse
from email_agent.intelligence.training import derive_training_from_user_events, fit_models
from email_agent.labelstudio.sync import pull_annotations
from email_agent.logging_setup import get_logger
from email_agent.models import HumanReview, db_session
from email_agent.workers.celery_app import app

log = get_logger(__name__)

REVIEW_EXPIRY_DAYS = 30


@app.task(name="email_agent.auto_archive_old")
def auto_archive_old_task() -> dict:
    """Ciclo diário estrito: arquiva e-mails Importante/Documento já lidos com mais de
    `archive_auto_min_age_days` (padrão 6 meses) que ainda estão na INBOX."""
    from email_agent.actions.archive_actions import auto_archive_old

    result = auto_archive_old(min_age_days=get_settings().archive_auto_min_age_days)
    log.info("auto_archive_task_done", **result)
    return result


@app.task(name="email_agent.nightly_maintenance")
def nightly_maintenance() -> dict:
    # Label Studio em modo PASSIVO: ainda puxamos anotações que você fizer lá (se
    # usar), mas não empurramos mais uma fila de rotulagem automaticamente — o sinal
    # primário de treino são seus movimentos na caixa (ver intelligence/training.py).
    # O push manual continua disponível via `email-agent train push-labelstudio`.
    pulled = 0
    try:
        pulled = pull_annotations()
    except Exception as exc:  # noqa: BLE001
        log.warning("labelstudio_pull_error", error=str(exc))

    derived = derive_training_from_user_events()
    fit = fit_models()
    trained = fit["spam_samples"]

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
    )
    return {
        "training_derived": derived,
        "trained_spam_samples": trained,
        "trained_category_samples": fit["category_samples"],
        "expired_reviews": expired,
        "labelstudio_pulled": pulled,
    }
