"""Pares (mensagem recebida → resposta escrita pelo usuário) extraídos do banco.

Matéria-prima da composição assistida: mostra como o usuário de fato responde.
Não chama LLM, não age no provedor e roda sobre o que o sync já persistiu.

Duas garantias que o resto do projeto depende:

1. O contexto de um exemplo só contém mensagens ANTERIORES à resposta. Incluir
   as posteriores colocaria a própria resposta dentro do enunciado — a métrica
   ficaria inflada e o prompt otimizado, inútil.
2. Quando a mensagem original não está no banco (IMAP só sincroniza
   INBOX/spam/sent/trash, então recebidas arquivadas faltam), o trecho citado
   dentro da própria resposta vira a mensagem recebida. É o que mantém o
   catálogo utilizável sem varrer a caixa inteira.
"""
import json
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from statistics import median

from sqlalchemy import select
from sqlalchemy.orm import Session

from email_agent.intelligence.thread_context import split_quoted_reply
from email_agent.logging_setup import get_logger
from email_agent.models import EmailAccount, EmailMessage

log = get_logger(__name__)

MAX_HISTORY_MESSAGES = 8
MAX_TEXT_CHARS = 4000
MAX_THREAD_LOOKBACK = 50

# Marcação de sobreposição: o corte por data garante que o contexto só tem
# mensagens anteriores, mas o usuário reenvia mensagens e reaproveita templates,
# então parte da resposta pode já estar no contexto por repetição legítima.
# Uma métrica de otimização precisa saber disso; o trecho curto é ignorado
# porque "Ok, obrigado" casa com qualquer coisa.
OVERLAP_PROBE_CHARS = 80
MIN_OVERLAP_CHARS = 40

SOURCE_THREAD = "thread"
SOURCE_QUOTE = "citacao"


@dataclass(frozen=True)
class ReplyExample:
    """Um exemplo de treino: o que chegou, o histórico até ali, o que foi respondido."""

    account_email: str
    thread_id: str | None
    reply_id: str
    reply_date: str | None
    subject: str | None
    recipients: list[str]
    incoming_source: str
    incoming_from: str | None
    incoming_date: str | None
    incoming_text: str
    history: list[dict]
    reply_text: str
    self_overlap: bool

    def as_record(self) -> dict:
        return asdict(self)


@dataclass
class CorpusStats:
    sent_total: int = 0
    examples: int = 0
    from_thread: int = 0
    from_quote: int = 0
    skipped_empty_reply: int = 0
    skipped_no_context: int = 0
    self_overlap: int = 0
    per_account: dict[str, int] = field(default_factory=dict)
    oldest: str | None = None
    newest: str | None = None
    median_reply_chars: int = 0


def _authored_text(message) -> str:
    """Só o que a pessoa escreveu, sem o trecho citado."""
    own, _quoted = split_quoted_reply(message.normalized_text or message.snippet or "")
    return own


def _iso(value) -> str | None:
    return value.isoformat() if value else None


def _prior_messages(session: Session, reply) -> list:
    """Mensagens da conversa anteriores à resposta — o corte que evita vazamento."""
    if not reply.provider_thread_id or reply.date is None:
        return []
    return list(
        session.execute(
            select(EmailMessage)
            .where(
                EmailMessage.account_id == reply.account_id,
                EmailMessage.provider_thread_id == reply.provider_thread_id,
                EmailMessage.date < reply.date,
            )
            .order_by(EmailMessage.date.asc())
            .limit(MAX_THREAD_LOOKBACK)
        ).scalars()
    )


def _history_entries(messages: list) -> list[dict]:
    return [
        {
            "date": _iso(item.date),
            "direction": "enviada" if item.is_sent_by_user else "recebida",
            "from": item.from_name or item.from_email,
            "subject": item.subject,
            "text": _authored_text(item)[:MAX_TEXT_CHARS],
        }
        for item in messages[-MAX_HISTORY_MESSAGES:]
    ]


def build_example(session: Session, reply, account_email: str) -> tuple[ReplyExample | None, str]:
    """Monta o exemplo de uma resposta. Retorna (exemplo, motivo) — motivo explica
    a recusa quando o exemplo é None."""
    authored, quoted = split_quoted_reply(reply.normalized_text or "")
    if not authored.strip():
        return None, "empty_reply"

    prior = _prior_messages(session, reply)
    incoming = next(
        (item for item in reversed(prior) if not item.is_sent_by_user), None
    )

    if incoming is not None:
        source = SOURCE_THREAD
        incoming_text = _authored_text(incoming)
        incoming_from = incoming.from_name or incoming.from_email
        incoming_date = _iso(incoming.date)
    elif quoted.strip():
        source = SOURCE_QUOTE
        incoming_text = quoted
        incoming_from = None
        incoming_date = None
    else:
        # Envio frio: não é resposta a nada, não serve de par.
        return None, "no_context"

    history = _history_entries(prior)
    example = ReplyExample(
        account_email=account_email,
        thread_id=reply.provider_thread_id,
        reply_id=reply.email_agent_id,
        reply_date=_iso(reply.date),
        subject=reply.subject,
        recipients=list(reply.to_json or []),
        incoming_source=source,
        incoming_from=incoming_from,
        incoming_date=incoming_date,
        incoming_text=incoming_text[:MAX_TEXT_CHARS],
        history=history,
        reply_text=authored[:MAX_TEXT_CHARS],
        self_overlap=_overlaps_context(authored, incoming_text, history),
    )
    return example, "ok"


def _overlaps_context(reply_text: str, incoming_text: str, history: list[dict]) -> bool:
    """A resposta já aparece no contexto? Acontece com reenvio e template reusado."""
    probe = reply_text.strip()[:OVERLAP_PROBE_CHARS]
    if len(probe) < MIN_OVERLAP_CHARS:
        return False
    if probe in incoming_text:
        return True
    return any(probe in (entry.get("text") or "") for entry in history)


def collect_reply_examples(
    session: Session,
    *,
    account_id: int | None = None,
    since_days: int | None = None,
) -> tuple[list[ReplyExample], CorpusStats]:
    """Percorre as mensagens enviadas e devolve os pares utilizáveis + estatísticas."""
    accounts = {
        account.id: account.email_address
        for account in session.execute(select(EmailAccount)).scalars()
    }

    statement = select(EmailMessage).where(EmailMessage.is_sent_by_user.is_(True))
    if account_id is not None:
        statement = statement.where(EmailMessage.account_id == account_id)
    if since_days is not None:
        cutoff = datetime.now(UTC) - timedelta(days=since_days)
        statement = statement.where(EmailMessage.date >= cutoff)
    statement = statement.order_by(EmailMessage.date.asc().nullslast())

    stats = CorpusStats()
    examples: list[ReplyExample] = []
    lengths: list[int] = []

    for reply in session.execute(statement).scalars():
        stats.sent_total += 1
        account_email = accounts.get(reply.account_id, "?")
        example, reason = build_example(session, reply, account_email)
        if example is None:
            if reason == "empty_reply":
                stats.skipped_empty_reply += 1
            else:
                stats.skipped_no_context += 1
            continue

        examples.append(example)
        lengths.append(len(example.reply_text))
        stats.examples += 1
        stats.per_account[account_email] = stats.per_account.get(account_email, 0) + 1
        if example.incoming_source == SOURCE_THREAD:
            stats.from_thread += 1
        else:
            stats.from_quote += 1
        if example.self_overlap:
            stats.self_overlap += 1
        if example.reply_date:
            stats.oldest = min(stats.oldest or example.reply_date, example.reply_date)
            stats.newest = max(stats.newest or example.reply_date, example.reply_date)

    stats.median_reply_chars = int(median(lengths)) if lengths else 0
    return examples, stats


def export_jsonl(examples: list[ReplyExample], path: Path) -> int:
    """Grava um exemplo por linha. O arquivo contém corpo de e-mail: fica em
    `data/exports/`, que é ignorado pelo Git."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for example in examples:
            handle.write(json.dumps(example.as_record(), ensure_ascii=False) + "\n")
    log.info("reply_corpus_exported", path=str(path), examples=len(examples))
    return len(examples)
