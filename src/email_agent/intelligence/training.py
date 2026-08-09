"""Aprendizado implícito (eventos do usuário -> eventos de treino com peso)
e retreinamento do modelo de spam.
"""
from datetime import datetime, timezone

import numpy as np
from sqlalchemy import func, select

from email_agent.config import get_settings
from email_agent.intelligence.category_model import CategoryModel
from email_agent.intelligence.features import email_features
from email_agent.intelligence.spam_model import SpamModel
from email_agent.intelligence.taxonomy import (
    CATEGORIES,
    LABEL_AGUARDANDO,
    LABEL_FISCAL,
    LABEL_IMPORTANTE,
    LABEL_MARKETING,
    LABEL_SPAM_SUSPEITO,
)
from email_agent.logging_setup import get_logger
from email_agent.models import (
    EmailActionLog,
    EmailClassification,
    EmailMessage,
    EmailTrainingEvent,
    EmailUserEvent,
    db_session,
)

# Fontes consideradas "rótulo manual" (decisão direta do usuário, confiável).
MANUAL_SOURCES = {"label_studio", "explicit_cli_feedback"}

log = get_logger(__name__)

# Rótulos do treino binário do modelo de spam. Todo rótulo "não-spam" precisa cair
# em HAM_LABELS para virar negativo (0) — senão o modelo treina só com positivos.
# Em especial "ignorar"/marketing/noticia/promocao são o grosso do sinal negativo
# natural do usuário (descartes), então têm de entrar aqui.
# Mínimo de exemplos por classe para considerá-la "pronta" para o ML decidir sozinho
# (heurística de prontidão usada por `train stats`, não bloqueia o treino).
MIN_EVENTS_PER_CLASS = 30

SPAM_LABELS = {"spam_suspeito"}
HAM_LABELS = {
    "ham", "documento", "documento_fiscal", "aguardando_resposta",
    "importante_p0", "importante_p1",
    "ignorar", "marketing", "noticia", "promocao",
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
            label, weight = _training_for_event(ev, ai, session=session)
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
        created += _derive_reply_importance(session)
    log.info("implicit_training_derived", created=created)
    return created


def _derive_reply_importance(session) -> int:
    """Você respondeu => o e-mail original importava. Marca `importante_p1` (peso 0.9)
    nos e-mails recebidos que tiveram resposta posterior sua na mesma thread. Dedup por
    marcador `reply:<id>` no campo reason."""
    created = 0
    processed = {
        r[0]
        for r in session.execute(
            select(EmailTrainingEvent.reason).where(
                EmailTrainingEvent.source == "implicit_event"
            )
        )
        if r[0] and r[0].startswith("reply:")
    }
    sent = (
        session.execute(
            select(EmailMessage).where(
                EmailMessage.is_sent_by_user.is_(True),
                EmailMessage.provider_thread_id.is_not(None),
                EmailMessage.date.is_not(None),
            )
        )
        .scalars()
        .all()
    )
    for s in sent:
        inbound = (
            session.execute(
                select(EmailMessage).where(
                    EmailMessage.account_id == s.account_id,
                    EmailMessage.provider_thread_id == s.provider_thread_id,
                    EmailMessage.is_sent_by_user.is_(False),
                    EmailMessage.date < s.date,
                )
            )
            .scalars()
            .all()
        )
        for msg in inbound:
            marker = f"reply:{msg.id}"
            if marker in processed:
                continue
            processed.add(marker)
            session.add(
                EmailTrainingEvent(
                    message_id=msg.id,
                    label="importante_p1",
                    source="implicit_event",
                    weight=0.9,
                    trusted=True,
                    reason=marker,
                )
            )
            created += 1
    return created


def _was_agent_label_action(session, message_id: int, label: str) -> bool:
    """True se a label foi aplicada pelo PRÓPRIO agente (consta em email_action_log).

    Regra 4 do MVP: decisão automática do agente não vira treino confiável. Quando o
    pipeline aplica AI/Importante (ou outra), o sync seguinte enxerga a mudança de
    label como se fosse ação do usuário — esta guarda evita treinar com a própria
    decisão. Só vale para ADIÇÕES; remoções de label AI são sempre do usuário (o
    pipeline nunca remove labels AI automaticamente)."""
    if session is None:
        return False
    row = session.execute(
        select(EmailActionLog.id).where(
            EmailActionLog.message_id == message_id,
            EmailActionLog.action_type == "add_label",
            EmailActionLog.status == "success",
            EmailActionLog.action_payload["label"].as_string() == label,
        )
    ).first()
    return row is not None


def _training_for_event(
    ev: EmailUserEvent, ai_labels: set[str], *, session=None
) -> tuple[str | None, float]:
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
        # Você respondeu => importava. (O caso geral é coberto por
        # _derive_reply_importance, varrendo threads; aqui fica o evento explícito.)
        return "importante_p1", 0.9
    if et == "removed_label" and LABEL_SPAM_SUSPEITO in (ev.previous_labels or []):
        return "ham", 0.8
    if et in ("added_label", "removed_label", "label_changed"):
        # A camada de sync grava mudanças de label como `label_changed` carregando
        # previous/new_labels (já resolvidos para NOMES de label AI no Gmail); deriva
        # o delta para tratar igual a added/removed_label.
        prev = set(ev.previous_labels or [])
        new = set(ev.new_labels or [])
        added = new - prev
        removed = prev - new

        # Negativo FORTE: você tirou AI/Importante de um e-mail que tinha sido marcado
        # como importante => sinal claro de que NÃO é importante. Vence tudo.
        if LABEL_IMPORTANTE in removed:
            return "ignorar", 1.0

        # Positivos por adição manual de label. Guarda: ignora se a adição foi do
        # próprio agente (consta em email_action_log) — só conta rótulo do usuário.
        if LABEL_IMPORTANTE in added and not _was_agent_label_action(
            session, ev.message_id, LABEL_IMPORTANTE
        ):
            return "importante_p1", 1.0
        if LABEL_FISCAL in added and not _was_agent_label_action(
            session, ev.message_id, LABEL_FISCAL
        ):
            return "documento_fiscal", 0.9
        if LABEL_AGUARDANDO in added and not _was_agent_label_action(
            session, ev.message_id, LABEL_AGUARDANDO
        ):
            return "aguardando_resposta", 0.9
        if LABEL_SPAM_SUSPEITO in added and not _was_agent_label_action(
            session, ev.message_id, LABEL_SPAM_SUSPEITO
        ):
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
        trusted_by_label = {
            str(label): int(n)
            for label, n in session.execute(
                select(EmailTrainingEvent.label, func.count())
                .where(EmailTrainingEvent.trusted.is_(True))
                .group_by(EmailTrainingEvent.label)
            ).all()
        }

    # Prontidão por classe: quanto de sinal confiável temos para cada categoria que
    # o ML precisa aprender (spam/importante/etc.). Mostra onde ainda falta rótulo.
    class_readiness = {
        cat: {
            "count": trusted_by_label.get(cat, 0),
            "ready": trusted_by_label.get(cat, 0) >= MIN_EVENTS_PER_CLASS,
        }
        for cat in CATEGORIES
    }

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
            "min_events_per_class": MIN_EVENTS_PER_CLASS,
        },
        "class_readiness": class_readiness,
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


def _collect_dataset(session) -> list[tuple[str, str, float]]:
    """(features, label, weight) de todos os eventos confiáveis com texto utilizável."""
    rows = (
        session.execute(
            select(EmailTrainingEvent).where(EmailTrainingEvent.trusted.is_(True))
        )
        .scalars()
        .all()
    )
    out: list[tuple[str, str, float]] = []
    for ev in rows:
        msg = session.get(EmailMessage, ev.message_id)
        if not msg or not msg.normalized_text:
            continue
        feats = email_features(msg.subject, msg.normalized_text, msg.from_email, msg.from_name)
        out.append((feats, ev.label, ev.weight))
    return out


def _holdout_report(texts, labels, weights, test_size: float) -> dict:
    """Treina um SGD limpo no split de treino e mede precision/recall/f1 por classe
    no split de teste. Espelha o modelo de produção (HashingVectorizer + SGD log_loss)."""
    from sklearn.feature_extraction.text import HashingVectorizer
    from sklearn.linear_model import SGDClassifier
    from sklearn.metrics import precision_recall_fscore_support
    from sklearn.model_selection import train_test_split

    classes = sorted(set(labels))
    if len(classes) < 2:
        return {"error": f"apenas uma classe presente ({classes}); impossível avaliar"}
    min_class = min(labels.count(c) for c in classes)
    if min_class < 2 or len(labels) < 8:
        return {"error": f"poucas amostras (classe menor tem {min_class})"}

    stratify = labels if min_class >= 2 else None
    idx = list(range(len(labels)))
    tr, te = train_test_split(idx, test_size=test_size, random_state=42, stratify=stratify)

    vec = HashingVectorizer(n_features=2**20, alternate_sign=False)
    Xtr = vec.transform([texts[i] for i in tr])
    clf = SGDClassifier(loss="log_loss", alpha=1e-5, random_state=42)
    clf.partial_fit(Xtr, [labels[i] for i in tr], classes=np.array(classes),
                    sample_weight=[weights[i] for i in tr])

    y_true = [labels[i] for i in te]
    y_pred = list(clf.predict(vec.transform([texts[i] for i in te])))
    p, r, f1, sup = precision_recall_fscore_support(
        y_true, y_pred, labels=classes, zero_division=0
    )
    per_class = {
        c: {"precision": round(float(p[i]), 3), "recall": round(float(r[i]), 3),
            "f1": round(float(f1[i]), 3), "support": int(sup[i])}
        for i, c in enumerate(classes)
    }
    correct = sum(1 for a, b in zip(y_true, y_pred) if a == b)
    return {
        "accuracy": round(correct / len(y_true), 3),
        "train_size": len(tr),
        "test_size": len(te),
        "per_class": per_class,
    }


def evaluate_models(test_size: float = 0.25) -> dict:
    """Avalia (holdout) a qualidade dos dois modelos no dataset confiável atual, para
    decidir o quanto confiar no ML antes de aumentar o cutoff. Não altera os modelos
    de produção nem consome eventos — treina cópias limpas só para medir."""
    with db_session() as session:
        data = _collect_dataset(session)

    if not data:
        return {"error": "sem dados de treino utilizáveis"}

    spam_t, spam_l, spam_w = [], [], []
    cat_t, cat_l, cat_w = [], [], []
    for feats, label, weight in data:
        binary = 1 if label in SPAM_LABELS else (0 if label in HAM_LABELS else None)
        if binary is not None:
            spam_t.append(feats); spam_l.append("spam" if binary else "ham"); spam_w.append(weight)
        if label in CATEGORIES:
            cat_t.append(feats); cat_l.append(label); cat_w.append(weight)

    return {
        "samples": len(data),
        "spam": _holdout_report(spam_t, spam_l, spam_w, test_size),
        "category": _holdout_report(cat_t, cat_l, cat_w, test_size),
    }
