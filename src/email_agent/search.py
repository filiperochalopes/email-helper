"""Busca local híbrida: full-text PostgreSQL + tolerância a erros com trigramas."""
from sqlalchemy import Select, func, literal, literal_column, or_, select

from email_agent.models import EmailClassification, EmailMessage


def email_search_statement(
    *,
    query: str | None = None,
    from_email: str | None = None,
    subject: str | None = None,
    category: str | None = None,
    priority: str | None = None,
    limit: int = 20,
) -> Select:
    """Monta a consulta usada por CLI e futura web, sem executar SQL bruto."""
    relevance = literal(0.0).label("relevance")
    conditions = []
    if query and query.strip():
        term = query.strip()
        ts_query = func.websearch_to_tsquery(literal_column("'simple'"), term)
        relevance = (
            func.ts_rank_cd(EmailMessage.search_vector, ts_query)
            + func.greatest(
                func.similarity(func.coalesce(EmailMessage.subject, ""), term),
                func.similarity(func.coalesce(EmailMessage.from_name, ""), term),
                func.similarity(func.coalesce(EmailMessage.from_email, ""), term),
            )
        ).label("relevance")
        conditions.append(
            or_(
                EmailMessage.search_vector.op("@@")(ts_query),
                EmailMessage.subject.op("%")(term),
                EmailMessage.from_name.op("%")(term),
                EmailMessage.from_email.op("%")(term),
            )
        )

    statement = (
        select(EmailMessage, EmailClassification, relevance)
        .outerjoin(EmailClassification, EmailClassification.message_id == EmailMessage.id)
    )
    if conditions:
        statement = statement.where(*conditions).order_by(relevance.desc(), EmailMessage.date.desc())
    else:
        statement = statement.order_by(EmailMessage.id.desc())
    if from_email:
        statement = statement.where(EmailMessage.from_email.ilike(f"%{from_email}%"))
    if subject:
        statement = statement.where(EmailMessage.subject.ilike(f"%{subject}%"))
    if category:
        statement = statement.where(EmailClassification.category == category)
    if priority:
        statement = statement.where(EmailClassification.priority == priority)
    return statement.limit(max(1, min(limit, 500)))
