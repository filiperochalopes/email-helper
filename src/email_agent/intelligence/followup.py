"""Quem deve o próximo e-mail nesta conversa.

Duas direções, deliberadamente separadas:

- `detect_followup_waiting_response`: o usuário escreveu e ninguém respondeu —
  é cobrança dele para fora;
- `detect_awaiting_my_reply`: um terceiro escreveu e o usuário ainda não
  respondeu — é o alvo da composição de rascunhos.

Ambas respondem só ao fato objetivo "já houve resposta posterior?". Se a mensagem
*merece* resposta é juízo da LLM, feito na triagem.
"""
import re

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from email_agent.models import EmailAccount, EmailMessage

QUESTION_PATTERNS = re.compile(
    r"\?|consegue|poderia|pode (me|nos)|aguardo|fico no aguardo|me confirm|"
    r"at[ée] (sexta|segunda|ter[çc]a|quarta|quinta|amanh[ãa]|o dia)|prazo|retorno",
    re.IGNORECASE,
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


def detect_awaiting_my_reply(session: Session, message: EmailMessage) -> tuple[bool, str | None]:
    """Terceiro escreveu e não há resposta minha posterior na thread.

    Não julga se a mensagem pede resposta — quem julga é a triagem. Aqui só se
    verifica o fato, que é o que a LLM não tem como saber sozinha.
    """
    if message.is_sent_by_user or message.date is None:
        return False, None
    if not message.provider_thread_id:
        return False, None

    account = session.get(EmailAccount, message.account_id)
    own_address = (account.email_address if account else "").lower()

    mine_later = session.execute(
        select(EmailMessage).where(
            EmailMessage.account_id == message.account_id,
            EmailMessage.provider_thread_id == message.provider_thread_id,
            EmailMessage.date > message.date,
            or_(
                EmailMessage.is_sent_by_user.is_(True),
                EmailMessage.from_email == own_address,
            ),
        )
    ).first()
    if mine_later:
        return False, None

    return True, "mensagem de terceiro sem resposta minha posterior na thread"
