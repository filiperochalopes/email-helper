from celery import Celery
from celery.schedules import crontab

from email_agent.config import get_settings
from email_agent.logging_setup import configure_logging

configure_logging()
settings = get_settings()

app = Celery(
    "email_agent",
    broker=settings.redis_url,
    backend=settings.redis_url,
    include=[
        "email_agent.workers.tasks_sync",
        "email_agent.workers.tasks_classify",
        "email_agent.workers.tasks_digest",
        "email_agent.workers.tasks_maintenance",
    ],
)

app.conf.timezone = settings.app_timezone
app.conf.task_acks_late = True
app.conf.worker_prefetch_multiplier = 1
app.conf.broker_connection_retry_on_startup = True

app.conf.beat_schedule = {
    # Manhã: sync -> classifica -> resumo no WhatsApp
    "sync-morning": {
        "task": "email_agent.sync_all_accounts",
        "schedule": crontab(hour=6, minute=40),
    },
    "classify-morning": {
        "task": "email_agent.classify_pending",
        "schedule": crontab(hour=6, minute=50),
    },
    "digest-morning": {
        "task": "email_agent.send_morning_digest",
        "schedule": crontab(hour=7, minute=0),
    },
    # Meio-dia e fim de tarde: incremental, sem WhatsApp
    "sync-noon": {"task": "email_agent.sync_all_accounts", "schedule": crontab(hour=12, minute=30)},
    "classify-noon": {"task": "email_agent.classify_pending", "schedule": crontab(hour=12, minute=35)},
    "sync-evening": {"task": "email_agent.sync_all_accounts", "schedule": crontab(hour=17, minute=30)},
    "classify-evening": {"task": "email_agent.classify_pending", "schedule": crontab(hour=17, minute=35)},
    # Noite: arquivamento automático (Importante/Documento lido > 6 meses) e manutenção
    "auto-archive-night": {
        "task": "email_agent.auto_archive_old",
        "schedule": crontab(hour=23, minute=15),
    },
    "maintenance-night": {
        "task": "email_agent.nightly_maintenance",
        "schedule": crontab(hour=23, minute=30),
    },
}
