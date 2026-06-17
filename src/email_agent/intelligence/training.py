"""Aprendizado implícito (eventos do usuário -> eventos de treino com peso)
e retreinamento do modelo de spam.
"""
from datetime import datetime, timezone

from sqlalchemy import func, select

from email_agent.config import get_settings
from email_agent.intelligence.category_model import CategoryModel
from email_agent.intelligence.features import email_features
from email_agent.intelligence.spam_model import SpamModel
from email_agent.intelligence.taxonomy import (
    CATEGORIES,
    LABEL_AGUARDANDO,
    LABEL_FISCAL,
    LABEL_MARKETING,
    LABEL_SPAM_SUSPEITO,
)
from email_agent.logging_setup import get_logger
from email_agent.models import (
    EmailClassification,
    EmailMessage,
    EmailTrainingEvent,
    EmailUserEvent,
    db_session,
)

# Fontes consideradas "rótulo manual" (decisão direta do usuário, confiável).
MANUAL_SOURCES = {"label_studio", "explicit_cli_feedback"}

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
            # Excluiu SEM remover o label AI/Spam Suspeito: confirma que o agente
            # acertou — sinal de peso máximo (reforço positivo).
            return "spam_suspeito", 1.0
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
    if et in ("added_label", "label_changed"):
        # A camada de sync grava mudanças de label como `label_changed` carregando
        # previous/new_labels; deriva o delta para tratar igual a added/removed_label.
        prev = set(ev.previous_labels or [])
        new = set(ev.new_labels or [])
        added = new - prev
        removed = prev - new
        if LABEL_FISCAL in added:
            return "documento_fiscal", 0.9
        if LABEL_AGUARDANDO in added:
            return "aguardando_resposta", 0.9
        if LABEL_SPAM_SUSPEITO in added:
            return "spam_suspeito", 0.9
        if LABEL_SPAM_SUSPEITO in removed:
            return "ham", 0.8
    return None, 0


def _grouped_counts(session, column) -> dict[str, int]:
    rows = session.execute(select(column, func.count()).group_by(column)).all()
    return {str(k): int(n) for k, n in rows}


def training_stats() -> dict:
    """Panorama do que já temos para treinar e do que o agente decidiu sozinho.

    Separa rótulos manuais (Label Studio + feedback CLI) dos implícitos (suas
    ações na caixa), mostra o feedback por mudança de status e o estado do modelo.
    """
    settings = get_settings()
    with db_session() as session:
        events_by_source = _grouped_counts(session, EmailTrainingEvent.source)
        events_by_label = _grouped_counts(session, EmailTrainingEvent.label)
        user_events_by_type = _grouped_counts(session, EmailUserEvent.event_type)
        auto_by_category = _grouped_counts(session, EmailClassification.category)
        auto_by_priority = _grouped_counts(session, EmailClassification.priority)

        manual = sum(n for s, n in events_by_source.items() if s in MANUAL_SOURCES)
        implicit = events_by_source.get("implicit_event", 0)
        pending = session.scalar(
            select(func.count()).select_from(EmailTrainingEvent).where(
                EmailTrainingEvent.trusted.is_(True),
                EmailTrainingEvent.consumed_at.is_(None),
            )
        )
        consumed = session.scalar(
            select(func.count()).select_from(EmailTrainingEvent).where(
                EmailTrainingEvent.consumed_at.is_not(None)
            )
        )

    spam = SpamModel()
    category = CategoryModel()
    return {
        "training_events": {
            "by_source": events_by_source,
            "by_label": events_by_label,
            "manual_labels": manual,
            "implicit_labels": implicit,
            "pending_fit": pending or 0,
            "already_consumed": consumed or 0,
            "min_events_to_fit": settings.training_min_events,
        },
        "user_feedback_by_event": user_events_by_type,
        "auto_classifications": {
            "by_category": auto_by_category,
            "by_priority": auto_by_priority,
        },
        "spam_model": {
            "trained": spam.is_trained,
            "path": spam.path,
        },
        "category_model": {
            "trained": category.is_trained,
            "path": category.path,
            "confidence_threshold": settings.category_confidence_threshold,
        },
    }


def fit_models() -> dict:
    """Treina os dois modelos com os eventos confiáveis ainda não consumidos:

    - **spam binário** (spam_suspeito vs ham) — `SpamModel`;
    - **categoria multiclasse** (todas as labels) — `CategoryModel`.

    Acumula até `TRAINING_MIN_EVENTS` no lote antes de treinar e consome o lote
    inteiro de uma vez (os dois modelos partilham o mesmo pool de eventos)."""
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
        usable: list[tuple[EmailTrainingEvent, str]] = []
        for ev in events:
            msg = session.get(EmailMessage, ev.message_id)
            if not msg or not msg.normalized_text:
                continue
            usable.append(
                (ev, email_features(msg.subject, msg.normalized_text, msg.from_email, msg.from_name))
            )

        if len(usable) < settings.training_min_events:
            log.info("fit_skipped_min_events", available=len(usable), min=settings.training_min_events)
            return {"spam_samples": 0, "category_samples": 0, "skipped": True}

        spam_t, spam_l, spam_w = [], [], []
        cat_t, cat_l, cat_w = [], [], []
        for ev, text in usable:
            binary = 1 if ev.label in SPAM_LABELS else (0 if ev.label in HAM_LABELS else None)
            if binary is not None:
                spam_t.append(text); spam_l.append(binary); spam_w.append(ev.weight)
            if ev.label in CATEGORIES:  # "ham" não é categoria: fica só no binário
                cat_t.append(text); cat_l.append(ev.label); cat_w.append(ev.weight)

        if spam_t:
            SpamModel().partial_fit(spam_t, spam_l, spam_w)
        if cat_t:
            CategoryModel().partial_fit(cat_t, cat_l, cat_w)

        now = datetime.now(timezone.utc)
        for ev, _ in usable:
            ev.consumed_at = now
    log.info("models_fitted", spam=len(spam_t), category=len(cat_t))
    return {"spam_samples": len(spam_t), "category_samples": len(cat_t), "skipped": False}


def fit_spam_model() -> int:
    """Compat: treina ambos os modelos, devolve nº de amostras do modelo de spam."""
    return fit_models()["spam_samples"]
