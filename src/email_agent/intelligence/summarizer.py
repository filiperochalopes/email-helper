"""Camada 3: LLM local via Ollama — apenas casos duvidosos, resumo e motivo legível.

O LLM nunca executa ações; só produz texto/JSON consultivo. O corpo do e-mail
nunca sai da máquina: o Ollama roda nativo no host macOS.
"""
from email_agent.intelligence.ollama_client import generate_json, parse_json_response
from email_agent.logging_setup import get_logger

log = get_logger(__name__)

# Reexportado para compatibilidade (testes e chamadas existentes).
_parse_json_response = parse_json_response

SUMMARY_PROMPT = """Você é um assistente de triagem de e-mails. Responda SOMENTE com JSON válido:
{{"summary": "resumo em 1 frase", "suggested_action": "ação sugerida em 1 frase", "spam_opinion": "spam|ham|incerto", "reason": "motivo curto"}}

E-mail:
De: {from_email}
Assunto: {subject}
Corpo (texto limpo, truncado):
{body}
"""


def llm_review(subject: str, from_email: str, body: str) -> dict | None:
    prompt = SUMMARY_PROMPT.format(
        from_email=from_email or "?", subject=subject or "(sem assunto)", body=body[:4000]
    )
    return generate_json(
        prompt, task="base", temperature=0.1,
        trace_name="llm_review",
        trace_metadata={"from": from_email, "subject": subject},
    )
