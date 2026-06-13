"""Agente abstraído de regras: avalia regras em linguagem natural via Ollama.

As regras são cadastradas em secrets/rules.yml (descrição em pt-BR + resultado) e
ficam em email_rule. Para cada e-mail, carregamos as regras da conta (+ globais) e
fazemos UMA chamada ao LLM passando todas as regras numeradas. O LLM diz quais se
aplicam e a prioridade resultante. O corpo nunca sai da máquina (Ollama local).
"""
import json
import re

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from email_agent.config import get_settings
from email_agent.logging_setup import get_logger
from email_agent.models import EmailRule

log = get_logger(__name__)

PROMPT = """Você é um classificador de e-mails. Avalie quais REGRAS se aplicam ao e-mail.
Responda SOMENTE com JSON: {{"matches": [{{"rule": <número>, "applies": true|false, "priority": "P0|P1|P2|ignore|null", "reason": "curto"}}]}}
Aplique uma regra apenas se o e-mail claramente se encaixa na descrição dela (incluindo as exceções).

REGRAS:
{rules_block}

E-MAIL:
De: {from_email}
Assunto: {subject}
Corpo (texto limpo, truncado):
{body}
"""


def load_rules_for_account(session: Session, account_email: str) -> list[EmailRule]:
    rules = (
        session.execute(select(EmailRule).where(EmailRule.is_active.is_(True), EmailRule.rule_type == "importance"))
        .scalars()
        .all()
    )
    out = []
    for r in rules:
        scope = (r.condition_json or {}).get("scope", "*")
        if scope in ("*", account_email):
            out.append(r)
    return out


def evaluate_rules_llm(account_email: str, subject: str, from_email: str, body: str, rules: list[EmailRule]) -> list[dict]:
    """Retorna a lista de outcomes das regras que o LLM considerou aplicáveis:
    [{"name", "priority", "labels", "category", "reason"}]."""
    if not rules:
        return []
    settings = get_settings()
    if not settings.llm_enabled:
        return []

    rules_block = "\n".join(
        f"{i}. {(r.condition_json or {}).get('description', r.name)}" for i, r in enumerate(rules, 1)
    )
    prompt = PROMPT.format(
        rules_block=rules_block, from_email=from_email or "?",
        subject=subject or "(sem assunto)", body=(body or "")[:4000],
    )
    try:
        resp = httpx.post(
            f"{settings.ollama_base_url}/api/generate",
            json={"model": settings.ollama_model, "prompt": prompt, "stream": False,
                  "format": "json", "options": {"temperature": 0.0}},
            timeout=120,
        )
        resp.raise_for_status()
        data = _parse_json(resp.json()["response"])
    except (httpx.HTTPError, json.JSONDecodeError, KeyError, ValueError) as exc:
        log.warning("rule_agent_failed", account=account_email, error=str(exc))
        return []

    outcomes = []
    for match in data.get("matches", []):
        if not match.get("applies"):
            continue
        idx = match.get("rule", 0)
        if not (1 <= idx <= len(rules)):
            continue
        rule = rules[idx - 1]
        action = rule.action_json or {}
        outcomes.append(
            {
                "name": rule.name,
                "priority": match.get("priority") or action.get("priority"),
                "labels": action.get("labels", []),
                "category": action.get("category"),
                "reason": f"regra '{rule.name}': {match.get('reason', '')}",
            }
        )
    return outcomes


def _parse_json(text: str) -> dict:
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if not m:
        raise ValueError(f"sem JSON: {text[:120]!r}")
    return json.loads(m.group(0))
