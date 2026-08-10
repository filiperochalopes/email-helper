"""Agente de priorização de P0 (roda só quando há muitos itens urgentes).

Quando o digest tem mais P0 do que cabe na mensagem, uma 2ª passada usa o modelo
*reasoning* (ex.: gemma maior) para reordenar por urgência real, considerando:
  - recência (e-mail mais novo tende a ser mais relevante);
  - exige resposta minha obrigatória e iminente;
  - conversa pessoal (não de empresa/newsletter/automático) pontua alto.

É consultivo: se o LLM falhar, mantemos a ordem original (por importance_score).
"""
from datetime import UTC, datetime

from email_agent.intelligence.ollama_client import generate_json
from email_agent.logging_setup import get_logger
from email_agent.models import EmailClassification, EmailMessage

log = get_logger(__name__)

PROMPT = """Você prioriza e-mails URGENTES (já marcados como P0) para um resumo.
Ordene do MAIS para o MENOS urgente seguindo, nesta ordem de peso:
1. Exige resposta minha obrigatória e iminente (prazo, alguém esperando minha ação).
2. Conversa pessoal/individual (pessoa real falando comigo) — pontua MUITO mais que
   empresa, newsletter, cobrança automática ou marketing.
3. Mais recente primeiro.

Responda SOMENTE com JSON: {{"order": ["E-...", "E-..."]}} com TODOS os ids recebidos.

E-MAILS:
{block}
"""


def _meta(m: EmailMessage, c: EmailClassification) -> dict:
    now = datetime.now(UTC)
    age_days = None
    if m.date:
        d = m.date if m.date.tzinfo else m.date.replace(tzinfo=UTC)
        age_days = max((now - d).days, 0)
    return {
        "id": m.email_agent_id,
        "from": m.from_email or "",
        "subject": (m.subject or "")[:120],
        "summary": (c.digest_summary or m.snippet or "")[:160],
        "age_days": age_days,
    }


def _block(metas: list[dict]) -> str:
    lines = []
    for x in metas:
        age = "?" if x["age_days"] is None else f"{x['age_days']}d"
        lines.append(
            f"- {x['id']} | de: {x['from']} | há {age} | {x['subject']} :: {x['summary']}"
        )
    return "\n".join(lines)


def _apply_order(pairs: list[tuple], order_ids: list[str]) -> list[tuple]:
    """Reordena pairs (msg, cls) pela ordem de ids dada. Ids desconhecidos são
    ignorados; pairs não citados mantêm a ordem original ao final."""
    by_id = {m.email_agent_id: (m, c) for m, c in pairs}
    seen: set[str] = set()
    out = []
    for eid in order_ids:
        if eid in by_id and eid not in seen:
            out.append(by_id[eid])
            seen.add(eid)
    for m, c in pairs:  # remanescentes preservam ordem original
        if m.email_agent_id not in seen:
            out.append((m, c))
    return out


def prioritize_p0(pairs: list[tuple]) -> list[tuple]:
    """Reordena a lista de (EmailMessage, EmailClassification) por urgência real.
    Em qualquer falha, devolve a lista de entrada inalterada."""
    if len(pairs) < 2:
        return pairs
    metas = [_meta(m, c) for m, c in pairs]
    data = generate_json(
        PROMPT.format(block=_block(metas)), task="reasoning", temperature=0.0,
        trace_name="prioritize_p0",
        trace_metadata={"emails_count": len(pairs)},
    )
    order = (data or {}).get("order") if isinstance(data, dict) else None
    if not order or not isinstance(order, list):
        return pairs
    log.info("p0_prioritized", count=len(pairs))
    return _apply_order(pairs, [str(x) for x in order])
