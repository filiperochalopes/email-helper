"""Contexto cronológico e sinais objetivos para a triagem por LLM."""
import re
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from email_agent.models import EmailAccount, EmailMessage

MAX_THREAD_MESSAGES = 12
MAX_MESSAGE_CHARS = 1600
MAX_THREAD_CHARS = 14000

_QUOTED_HEADER = re.compile(
    r"^(em .+escreveu:|on .+wrote:|-{2,}\s*original message\s*-{2,})$",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class ThreadContext:
    history: str
    message_date: str
    current_date: str


def split_quoted_reply(text: str) -> tuple[str, str]:
    """Separa o que foi escrito agora do trecho citado da mensagem anterior.

    A triagem descarta a citação (ruído); o catálogo de respostas usa justamente
    ela para recuperar a mensagem recebida quando o original não está no banco.
    """
    lines = (text or "").splitlines()
    for index, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith(">") or _QUOTED_HEADER.match(stripped):
            own = "\n".join(lines[:index]).strip()
            return own, _strip_quote_markers("\n".join(lines[index:]))
    return "\n".join(lines).strip(), ""


def _strip_quote_markers(text: str) -> str:
    return "\n".join(re.sub(r"^\s*>+ ?", "", line) for line in text.splitlines()).strip()


def _without_quoted_reply(text: str) -> str:
    return split_quoted_reply(text)[0]


def _direction(message: EmailMessage) -> str:
    return "usuário → terceiro" if message.is_sent_by_user else "terceiro → usuário"


def _age_in_days(now: datetime, value: datetime | None) -> int | None:
    if value is None:
        return None
    comparable_now = now
    if value.tzinfo is None and now.tzinfo is not None:
        comparable_now = now.replace(tzinfo=None)
    elif value.tzinfo is not None and now.tzinfo is None:
        comparable_now = now.replace(tzinfo=UTC)
    return (comparable_now - value).days


def build_thread_context(
    session: Session,
    message: EmailMessage,
    *,
    now: datetime | None = None,
) -> ThreadContext:
    now = now or datetime.now(UTC)
    message_date = message.date.isoformat() if message.date else "desconhecida"
    current_date = now.isoformat()
    age = _age_in_days(now, message.date)

    if not message.provider_thread_id:
        history = (
            "Histórico indisponível: a mensagem não possui identificador de conversa.\n"
            f"Idade da mensagem: {age if age is not None else 'desconhecida'} dias."
        )
        return ThreadContext(history, message_date, current_date)

    rows = list(
        session.execute(
            select(EmailMessage)
            .where(
                EmailMessage.account_id == message.account_id,
                EmailMessage.provider_thread_id == message.provider_thread_id,
            )
            .order_by(EmailMessage.date.desc().nullslast(), EmailMessage.id.desc())
            .limit(50)
        ).scalars()
    )
    rows.reverse()
    account = session.get(EmailAccount, message.account_id)
    own_address = (account.email_address if account else "").lower()
    last = rows[-1] if rows else message
    later_third_party = any(
        item.date
        and message.date
        and item.date > message.date
        and not item.is_sent_by_user
        and (item.from_email or "").lower() != own_address
        for item in rows
    )

    visible = rows[-MAX_THREAD_MESSAGES:]
    truncated = len(rows) > len(visible)
    entries: list[str] = []
    for item in visible:
        marker = " [MENSAGEM AVALIADA]" if item.id == message.id else ""
        body = _without_quoted_reply(item.normalized_text or item.snippet or "")
        body = body[:MAX_MESSAGE_CHARS] or "(sem corpo disponível)"
        entries.append(
            f"- [{item.date.isoformat() if item.date else 'data desconhecida'}] "
            f"{_direction(item)}{marker}\n"
            f"  De: {item.from_name or item.from_email or '?'}\n"
            f"  Assunto: {item.subject or '(sem assunto)'}\n"
            f"  Conteúdo: {body}"
        )

    signals = [
        f"Mensagens conhecidas na conversa: {len(rows)}"
        + (f" (mostrando as {len(visible)} mais recentes)" if truncated else ""),
        f"Idade da mensagem avaliada: {age if age is not None else 'desconhecida'} dias",
        f"Última mensagem conhecida: {_direction(last)}",
        "Existe resposta posterior de terceiro à mensagem avaliada: "
        + ("sim" if later_third_party else "não"),
    ]
    history = "Sinais da conversa:\n- " + "\n- ".join(signals)
    history += "\n\nHistórico cronológico:\n" + "\n".join(entries)
    return ThreadContext(history[:MAX_THREAD_CHARS], message_date, current_date)
