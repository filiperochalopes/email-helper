"""Cliente Evolution API v2 (WhatsApp) — somente saída de texto no MVP.

Atenção: payload v2 é plano ({"number", "text"}); o formato antigo
{"textMessage": {"text": ...}} é da v1 e não funciona na v2.
https://doc.evolution-api.com/v2/api-reference/message-controller/send-text
"""
import httpx

from email_agent.config import get_settings
from email_agent.logging_setup import get_logger

log = get_logger(__name__)


def send_text(text: str, number: str | None = None) -> dict:
    settings = get_settings()
    number = number or settings.whatsapp_summary_number
    if not (settings.evolution_base_url and settings.evolution_api_key and number):
        raise RuntimeError("Evolution API não configurada (.env: EVOLUTION_* e WHATSAPP_SUMMARY_NUMBER)")

    url = f"{settings.evolution_base_url.rstrip('/')}/message/sendText/{settings.evolution_instance_name}"
    payload = {"number": number, "text": text, "linkPreview": False}
    resp = httpx.post(
        url,
        json=payload,
        headers={"apikey": settings.evolution_api_key},
        timeout=30,
    )
    resp.raise_for_status()
    log.info("whatsapp_sent", chars=len(text), status=resp.status_code)
    return resp.json()
