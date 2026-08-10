"""Geração do ID interno curto E-YYYYMMDD-NNNNNN.

A sequência é global (não reinicia por dia): a data no ID é apenas legibilidade
(data de ingestão). Usa uma sequence do PostgreSQL para ser segura sob concorrência.
"""
from datetime import UTC, datetime

from sqlalchemy import text
from sqlalchemy.orm import Session

SEQUENCE_NAME = "email_agent_id_seq"


def generate_email_agent_id(session: Session, when: datetime | None = None) -> str:
    when = when or datetime.now(UTC)
    session.execute(text(f"CREATE SEQUENCE IF NOT EXISTS {SEQUENCE_NAME}"))
    n = session.execute(text(f"SELECT nextval('{SEQUENCE_NAME}')")).scalar_one()
    return f"E-{when:%Y%m%d}-{n:06d}"
