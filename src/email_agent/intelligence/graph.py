"""Workflow LangGraph: classifica uma mensagem já persistida e aplica ações seguras."""
from langgraph.graph import END, START, StateGraph

from email_agent.actions.safety_gate import plan_safe_actions
from email_agent.intelligence.classifier import classify
from email_agent.intelligence.followup import detect_followup_waiting_response
from email_agent.intelligence.rule_agent import evaluate_rules_llm, load_rules_for_account
from email_agent.intelligence.spam_model import SpamModel
from email_agent.intelligence.state import EmailAgentState
from email_agent.intelligence.summarizer import llm_review
from email_agent.intelligence.taxonomy import LABEL_AGUARDANDO
from email_agent.models import EmailAccount, EmailClassification, EmailMessage, db_session

_spam_model: SpamModel | None = None


def _get_spam_model() -> SpamModel:
    global _spam_model
    if _spam_model is None:
        _spam_model = SpamModel()
    return _spam_model


def load_email(state: EmailAgentState) -> EmailAgentState:
    with db_session() as session:
        msg = session.get(EmailMessage, state["db_message_id"])
        if msg is None:
            return {"errors": ["mensagem não encontrada no banco"]}
        return {
            "account_id": msg.account_id,
            "email_agent_id": msg.email_agent_id,
            "provider_message_id": msg.provider_message_id,
            "provider_thread_id": msg.provider_thread_id,
            "mailbox": msg.mailbox,
            "from_email": msg.from_email or "",
            "subject": msg.subject or "",
            "normalized_text": msg.normalized_text or "",
            "is_sent_by_user": msg.is_sent_by_user,
            "current_provider_labels": msg.raw_labels or [],
            "current_ai_labels": msg.ai_labels or [],
            "attachments": [
                {"filename": a.filename, "content_type": a.content_type} for a in msg.attachments
            ],
            "has_list_unsubscribe": bool((msg.raw_labels or []) and False) or state.get(
                "has_list_unsubscribe", False
            ),
            "errors": [],
        }


def classify_message(state: EmailAgentState) -> EmailAgentState:
    in_spam = state.get("mailbox", "").upper() in ("SPAM", "JUNK") or "SPAM" in [
        l.upper() for l in state.get("current_provider_labels", [])
    ]
    result = classify(
        subject=state.get("subject", ""),
        normalized_text=state.get("normalized_text", ""),
        from_email=state.get("from_email"),
        has_list_unsubscribe=state.get("has_list_unsubscribe", False),
        attachment_filenames=[a.get("filename") or "" for a in state.get("attachments", [])],
        attachment_types=[a.get("content_type") or "" for a in state.get("attachments", [])],
        in_provider_spam=in_spam,
        spam_model=_get_spam_model(),
    )
    return {
        "spam_score": result.spam_score,
        "spam_reason": "; ".join(result.reasons),
        "importance_score": result.importance_score,
        "importance_reason": "; ".join(result.reasons),
        "priority": result.priority,  # type: ignore[typeddict-item]
        "category": result.category,
        "confidence": result.confidence,
        "suggested_labels": result.suggested_labels,
        "needs_human_review": result.needs_human_review,
        "human_review_reason": "; ".join(result.reasons) if result.needs_human_review else None,
    }


def detect_followup(state: EmailAgentState) -> EmailAgentState:
    if not state.get("is_sent_by_user"):
        return {"is_followup_waiting_response": False}
    with db_session() as session:
        msg = session.get(EmailMessage, state["db_message_id"])
        waiting, reason = detect_followup_waiting_response(session, msg)
    if waiting:
        labels = list(state.get("suggested_labels", []))
        if LABEL_AGUARDANDO not in labels:
            labels.append(LABEL_AGUARDANDO)
        return {
            "is_followup_waiting_response": True,
            "followup_reason": reason,
            "category": "aguardando_resposta",
            "priority": "P1",
            "suggested_labels": labels,
        }
    return {"is_followup_waiting_response": False}


_PRIORITY_RANK = {"P0": 3, "P1": 2, "P2": 1, "ignore": 0}


def apply_rules(state: EmailAgentState) -> EmailAgentState:
    """Agente abstraído: avalia as regras (rules.yml) da conta via LLM e ajusta
    prioridade/categoria/labels. Sempre via LLM (decisão do usuário)."""
    with db_session() as session:
        account = session.get(EmailAccount, state["account_id"])
        rules = load_rules_for_account(session, account.email_address)
    if not rules:
        return {}
    outcomes = evaluate_rules_llm(
        account.email_address, state.get("subject", ""), state.get("from_email", ""),
        state.get("normalized_text", ""), rules,
    )
    if not outcomes:
        return {}

    out: EmailAgentState = {}
    labels = list(state.get("suggested_labels", []))
    best_priority = state.get("priority", "P2")
    reasons = []
    new_category = None
    for oc in outcomes:
        if oc.get("priority") and _PRIORITY_RANK.get(oc["priority"], -1) > _PRIORITY_RANK.get(best_priority, -1):
            best_priority = oc["priority"]
        for lb in oc.get("labels", []):
            if lb not in labels:
                labels.append(lb)
        if oc.get("category"):
            new_category = oc["category"]
        reasons.append(oc["reason"])

    out["priority"] = best_priority  # type: ignore[typeddict-item]
    out["suggested_labels"] = labels
    if new_category:
        out["category"] = new_category
    existing = state.get("importance_reason", "")
    out["importance_reason"] = "; ".join(filter(None, [existing] + reasons))
    return out


def llm_node(state: EmailAgentState) -> EmailAgentState:
    """Camada 3: só roda em incerteza ou se for entrar no resumo (gera summary legível)."""
    uncertain = state.get("needs_human_review") or state.get("confidence", 1.0) < 0.6
    in_digest = state.get("priority") in ("P0", "P1")
    if not (uncertain or in_digest):
        return {}
    review = llm_review(
        state.get("subject", ""), state.get("from_email", ""), state.get("normalized_text", "")
    )
    if not review:
        return {}
    out: EmailAgentState = {"digest_summary": review.get("summary")}
    if uncertain and review.get("spam_opinion") == "incerto":
        out["needs_human_review"] = True
    return out


def safety_gate(state: EmailAgentState) -> EmailAgentState:
    return plan_safe_actions(state)


def persist_result(state: EmailAgentState) -> EmailAgentState:
    with db_session() as session:
        msg = session.get(EmailMessage, state["db_message_id"])
        # Uma classificação por mensagem: re-relabel substitui a anterior.
        session.query(EmailClassification).filter_by(message_id=msg.id).delete()
        session.add(
            EmailClassification(
                message_id=msg.id,
                spam_score=state.get("spam_score"),
                spam_reason=state.get("spam_reason"),
                importance_score=state.get("importance_score"),
                importance_reason=state.get("importance_reason"),
                priority=state.get("priority"),
                category=state.get("category"),
                action_required=state.get("priority") in ("P0", "P1"),
                digest_summary=state.get("digest_summary"),
                suggested_labels=state.get("suggested_labels"),
                model_name="rules+sgd+ollama",
                confidence=state.get("confidence"),
            )
        )
        msg.ai_labels = sorted(
            set(msg.ai_labels or []) | set(state.get("suggested_labels", []))
        )
    return {
        "digest_include": state.get("priority") in ("P0", "P1"),
        "digest_priority": state.get("priority", "none"),  # type: ignore[typeddict-item]
    }


def build_graph():
    g = StateGraph(EmailAgentState)
    g.add_node("load_email", load_email)
    g.add_node("classify_message", classify_message)
    g.add_node("detect_followup", detect_followup)
    g.add_node("apply_rules", apply_rules)
    g.add_node("llm_node", llm_node)
    g.add_node("safety_gate", safety_gate)
    g.add_node("persist_result", persist_result)

    g.add_edge(START, "load_email")
    g.add_edge("load_email", "classify_message")
    g.add_edge("classify_message", "detect_followup")
    g.add_edge("detect_followup", "apply_rules")
    g.add_edge("apply_rules", "llm_node")
    g.add_edge("llm_node", "safety_gate")
    g.add_edge("safety_gate", "persist_result")
    g.add_edge("persist_result", END)
    return g.compile()


def run_pipeline(db_message_id: int) -> EmailAgentState:
    graph = build_graph()
    return graph.invoke({"db_message_id": db_message_id})
