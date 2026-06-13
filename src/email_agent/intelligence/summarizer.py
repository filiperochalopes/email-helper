"""Camada 3: LLM local via Ollama — apenas casos duvidosos, resumo e motivo legível.

O LLM nunca executa ações; só produz texto/JSON consultivo. O corpo do e-mail
nunca sai da máquina: o Ollama roda nativo no host macOS.
"""
import json
import re

import httpx

from email_agent.config import get_settings
from email_agent.logging_setup import get_logger

log = get_logger(__name__)

SUMMARY_PROMPT = """Você é um assistente de triagem de e-mails. Responda SOMENTE com JSON válido:
{{"summary": "resumo em 1 frase", "suggested_action": "ação sugerida em 1 frase", "spam_opinion": "spam|ham|incerto", "reason": "motivo curto"}}

E-mail:
De: {from_email}
Assunto: {subject}
Corpo (texto limpo, truncado):
{body}
"""


def llm_review(subject: str, from_email: str, body: str) -> dict | None:
    settings = get_settings()
    if not settings.llm_enabled:
        return None
    prompt = SUMMARY_PROMPT.format(
        from_email=from_email or "?", subject=subject or "(sem assunto)", body=body[:4000]
    )
    try:
        resp = httpx.post(
            f"{settings.ollama_base_url}/api/generate",
            json={
                "model": settings.ollama_model,
                "prompt": prompt,
                "stream": False,
                "format": "json",
                "options": {"temperature": 0.1},
            },
            timeout=120,
        )
        resp.raise_for_status()
        return _parse_json_response(resp.json()["response"])
    except (httpx.HTTPError, json.JSONDecodeError, KeyError, ValueError) as exc:
        log.warning("llm_review_failed", error=str(exc))
        return None


def _parse_json_response(text: str) -> dict:
    """Alguns modelos (ex.: builds MLX) ignoram format=json e devolvem o JSON
    dentro de cerca markdown ou com texto ao redor — extrai o primeiro objeto."""
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        raise ValueError(f"resposta sem JSON: {text[:120]!r}")
    return json.loads(match.group(0))
