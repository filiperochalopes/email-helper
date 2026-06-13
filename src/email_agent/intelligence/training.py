"""Aprendizado implícito (eventos do usuário -> eventos de treino com peso)
e retreinamento do modelo de spam.
"""
from datetime import datetime, timezone

from sqlalchemy import select

from email_agent.config import get_settings
from email_agent.intelligence.spam_model import SpamModel
from email_agent.intelligence.taxonomy import (
    LABEL_AGUARDANDO,
    LABEL_FISCAL,
    LABEL_MARKETING,
    LABEL_SPAM_SUSPEITO,
)
from email_agent.logging_setup import get_logger
from email_agent.models import (
    EmailMessage,
    EmailTrainingEvent,
    EmailUserEvent,
    db_session,
)

log = get_logger(__name__)

# label de treino binário do modelo de spam
SPAM_LABELS = {"spam_suspeito"}
HAM_LABELS = {
    "ham", "documento", "documento_fiscal", "aguardando_resposta",
    "importante_p0", "importante_p1",
}


def derive_training_from_user_events() -> int:
    """Converte eventos de usuário ainda não processados em eventos de treino,
    aplicando as regras de peso do plano (seção 14)."""
    created = 0
    with db_session() as session:
        processed_event_ids = {
            r[0]
            for r in session.execute(
                select(EmailTrainingEvent.reason).where(
                    EmailTrainingEvent.source == "implicit_event"
                )
            )
            if r[0] and r[0].startswith("event:")
        }
        events = session.execute(select(EmailUserEvent)).scalars().all()
        for ev in events:
            marker = f"event:{ev.id}"
            if marker in processed_event_ids:
                continue
            msg = session.get(EmailMessage, ev.message_id)
            if msg is None:
                continue
            ai = set(msg.ai_labels or [])
            label, weight = _training_for_event(ev, ai)
            if label is None:
                continue
            session.add(
                EmailTrainingEvent(
                    message_id=msg.id,
                    label=label,
                    source="implicit_event",
                    weight=weight,
                    trusted=True,
                    reason=f"{marker} {ev.event_type}",
                )
            )
            created += 1
    log.info("implicit_training_derived", created=created)
    return created


def _training_for_event(ev: EmailUserEvent, ai_labels: set[str]) -> tuple[str | None, float]:
    et = ev.event_type
    if et == "moved_to_trash":
        if LABEL_SPAM_SUSPEITO in ai_labels:
            return "spam_suspeito", 0.8
        if LABEL_MARKETING in ai_labels:
            return "ignorar", 0.6
        if LABEL_FISCAL in ai_labels:
            return "ignorar", 0.5
        if LABEL_AGUARDANDO in ai_labels:
            return None, 0  # follow-up resolvido, não é spam nem treino
        return None, 0  # descarte sem contexto: não treinar como spam
    if et == "moved_from_spam_to_inbox":
        return "ham", 0.9
    if et == "moved_to_spam":
        return "spam_suspeito", 0.8
    if et == "replied_by_user":
        return "ham", 0.9
    if et == "removed_label" and LABEL_SPAM_SUSPEITO in (ev.previous_labels or []):
        return "ham", 0.8
    if et == "added_label":
        new = set(ev.new_labels or []) - set(ev.previous_labels or [])
        if LABEL_FISCAL in new:
            return "documento_fiscal", 0.9
        if LABEL_AGUARDANDO in new:
            return "aguardando_resposta", 0.9
        if LABEL_SPAM_SUSPEITO in new:
            return "spam_suspeito", 0.9
    return None, 0


def fit_spam_model() -> int:
    """partial_fit com eventos de treino confiáveis ainda não consumidos."""
    settings = get_settings()
    with db_session() as session:
        events = (
            session.execute(
                select(EmailTrainingEvent).where(
                    EmailTrainingEvent.trusted.is_(True),
                    EmailTrainingEvent.consumed_at.is_(None),
                )
            )
            .scalars()
            .all()
        )
        texts, labels, weights, consumed = [], [], [], []
        for ev in events:
            binary = 1 if ev.label in SPAM_LABELS else (0 if ev.label in HAM_LABELS else None)
            if binary is None:
                ev.consumed_at = datetime.now(timezone.utc)  # categoria não-binária: marca e pula
                continue
            msg = session.get(EmailMessage, ev.message_id)
            if not msg or not msg.normalized_text:
                continue
            texts.append(f"{msg.subject or ''}\n{msg.normalized_text}")
            labels.append(binary)
            weights.append(ev.weight)
            consumed.append(ev)

        if len(texts) < settings.training_min_events:
            log.info("fit_skipped_min_events", available=len(texts), min=settings.training_min_events)
            return 0

        model = SpamModel()
        model.partial_fit(texts, labels, weights)
        now = datetime.now(timezone.utc)
        for ev in consumed:
            ev.consumed_at = now
    log.info("spam_model_fitted", samples=len(texts))
    return len(texts)
