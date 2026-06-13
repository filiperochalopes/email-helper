"""Detecção de follow-up: mensagem enviada pelo usuário aguardando resposta.

Analisa a thread: se a última mensagem é do usuário, contém pergunta/solicitação
e não houve resposta posterior de terceiros, marca aguardando_resposta.
"""
import re

from sqlalchemy import select
from sqlalchemy.orm import Session

from email_agent.models import EmailAccount, EmailMessage

QUESTION_PATTERNS = re.compile(
    r"\?|consegue|poderia|pode (me|nos)|aguardo|fico no aguardo|me confirm|"
    r"at[ée] (sexta|segunda|ter[çc]a|quarta|quinta|amanh[ãa]|o dia)|prazo|retorno",
    re.I,
)


def detect_followup_waiting_response(session: Session, message: EmailMessage) -> tuple[bool, str | None]:
    if not message.is_sent_by_user:
        return False, None

    text = f"{message.subject or ''}\n{message.normalized_text or ''}"
    if not QUESTION_PATTERNS.search(text):
        return False, None

    account = session.get(EmailAccount, message.account_id)
    own_address = (account.email_address if account else "").lower()

    # Houve resposta posterior de terceiro na mesma thread?
    if message.provider_thread_id:
        later = session.execute(
            select(EmailMessage).where(
                EmailMessage.account_id == message.account_id,
                EmailMessage.provider_thread_id == message.provider_thread_id,
                EmailMessage.date > message.date,
                EmailMessage.from_email != own_address,
            )
        ).first()
        if later:
            return False, None

    return True, "mensagem enviada com pergunta/solicitação e sem resposta posterior na thread"
