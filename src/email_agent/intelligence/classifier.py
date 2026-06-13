"""Combina regras (camada 1), modelo estatístico (camada 2) e decide
categoria, prioridade e labels sugeridas. A camada 3 (LLM) é acionada
apenas em casos duvidosos, em summarizer.py.
"""
from dataclasses import dataclass, field

from email_agent.intelligence.rules import RuleResult, evaluate_rules
from email_agent.intelligence.spam_model import SpamModel
from email_agent.intelligence.taxonomy import (
    CATEGORY_TO_LABELS,
    LABEL_REVISAR,
)

SPAM_THRESHOLD = 0.75
UNCERTAIN_BAND = (0.40, 0.75)


@dataclass
class Classification:
    category: str
    priority: str  # P0|P1|P2|ignore
    spam_score: float
    importance_score: float
    suggested_labels: list[str]
    needs_human_review: bool
    reasons: list[str] = field(default_factory=list)
    confidence: float = 0.5


def classify(
    *,
    subject: str,
    normalized_text: str,
    from_email: str | None,
    has_list_unsubscribe: bool,
    attachment_filenames: list[str],
    attachment_types: list[str],
    in_provider_spam: bool,
    vip_domains: set[str] | None = None,
    blocked_domains: set[str] | None = None,
    spam_model: SpamModel | None = None,
) -> Classification:
    rules: RuleResult = evaluate_rules(
        subject=subject,
        normalized_text=normalized_text,
        from_email=from_email,
        has_list_unsubscribe=has_list_unsubscribe,
        attachment_filenames=attachment_filenames,
        attachment_types=attachment_types,
        in_provider_spam=in_provider_spam,
        vip_domains=vip_domains or set(),
        blocked_domains=blocked_domains or set(),
    )

    spam_score = rules.spam_score
    model_proba = None
    if spam_model and spam_model.is_trained:
        model_proba = spam_model.predict_proba_spam(f"{subject}\n{normalized_text}")
        if model_proba is not None:
            spam_score = 0.5 * spam_score + 0.5 * model_proba
            rules.reasons.append(f"modelo estatístico: p(spam)={model_proba:.2f}")

    votes = dict(rules.category_votes)
    needs_review = votes.get("revisar", 0) >= 1.0
    importance = rules.importance_score

    if spam_score >= SPAM_THRESHOLD and importance < 25:
        category = "spam_suspeito"
    elif UNCERTAIN_BAND[0] <= spam_score < UNCERTAIN_BAND[1] and importance >= 25:
        category, needs_review = "revisar", True
        rules.reasons.append("conflito: sinais de spam e de importância simultâneos")
    elif importance >= 70:
        category = "importante_p0"
    elif importance >= 45:
        category = "importante_p1"
    elif votes:
        category = max(votes, key=lambda k: votes[k])
    else:
        category = "ignorar"

    if needs_review:
        category = "revisar"

    priority = {
        "importante_p0": "P0",
        "importante_p1": "P1",
        "revisar": "P1" if importance >= 45 else "P2",
        "documento_fiscal": "P1",
        "documento": "P2",
        "aguardando_resposta": "P1",
        "marketing": "ignore",
        "noticia": "ignore",
        "promocao": "ignore",
        "spam_suspeito": "ignore",
        "ignorar": "ignore",
    }.get(category, "P2")

    labels = list(CATEGORY_TO_LABELS.get(category, []))
    if needs_review and LABEL_REVISAR not in labels:
        labels.append(LABEL_REVISAR)
    # Labels de conteúdo coexistem com a categoria principal (ex.: fiscal + importante)
    for cat in ("documento_fiscal", "documento"):
        if votes.get(cat, 0) >= 0.5 and category not in ("spam_suspeito",):
            for lb in CATEGORY_TO_LABELS[cat]:
                if lb not in labels:
                    labels.append(lb)

    top_vote = max(votes.values(), default=0.0)
    confidence = min(0.95, 0.4 + 0.3 * top_vote + (0.2 if model_proba is not None else 0.0))

    return Classification(
        category=category,
        priority=priority,
        spam_score=round(spam_score, 3),
        importance_score=round(importance, 1),
        suggested_labels=labels,
        needs_human_review=needs_review,
        reasons=rules.reasons,
        confidence=round(confidence, 2),
    )
