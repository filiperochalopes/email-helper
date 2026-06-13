import time

from email_agent.config import get_settings
from email_agent.connectors.evolution_client import send_text
from email_agent.digest.builder import build_digest, save_digest
from email_agent.logging_setup import get_logger
from email_agent.workers.celery_app import app

log = get_logger(__name__)


@app.task(name="email_agent.send_morning_digest", max_retries=3, autoretry_for=(Exception,),
          retry_backoff=60)
def send_morning_digest() -> dict:
    digest = build_digest()
    number = get_settings().whatsapp_summary_number
    messages = digest.messages()
    try:
        for i, body in enumerate(messages):
            send_text(body)
            if i < len(messages) - 1:
                time.sleep(1.5)  # evita reordenação/rate limit no WhatsApp
        save_digest("\n\n---\n\n".join(messages), number, "sent")
        return {"status": "sent", "messages": len(messages)}
    except Exception as exc:  # noqa: BLE001
        save_digest("\n\n---\n\n".join(messages), number, "error")
        log.error("digest_send_failed", error=str(exc))
        raise
