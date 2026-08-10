"""Pipeline explícito de triagem: Ollama local → regras → safety gate → persistência.

O nome do módulo é mantido apenas por ser o ponto de entrada do pipeline; não há
mais LangGraph, modelos sklearn ou treinamento noturno.
"""
from email_agent.actions.safety_gate import plan_safe_actions
from email_agent.intelligence.followup import detect_followup_waiting_response
from email_agent.intelligence.rule_agent import evaluate_rules_llm, load_rules_for_account
from email_agent.intelligence.state import EmailAgentState
from email_agent.intelligence.triage import triage_email
from email_agent.models import EmailAccount, EmailClassification, EmailMessage, db_session


def load_email(state: EmailAgentState) -> EmailAgentState:
    with db_session() as session:
        msg = session.get(EmailMessage, state["db_message_id"])
        if msg is None:
            return {"errors": ["mensagem não encontrada no banco"]}
        account = session.get(EmailAccount, msg.account_id)
        return {
            "account_id": msg.account_id,
            "account_email": account.email_address if account else "",
            "email_agent_id": msg.email_agent_id,
            "provider_message_id": msg.provider_message_id,
            "provider_thread_id": msg.provider_thread_id,
            "mailbox": msg.mailbox,
            "from_email": msg.from_email or "",
            "from_name": msg.from_name or "",
            "subject": msg.subject or "",
            "normalized_text": msg.normalized_text or "",
            "is_sent_by_user": msg.is_sent_by_user,
            "current_provider_labels": msg.raw_labels or [],
            "current_ai_labels": msg.ai_labels or [],
            "attachments": [
                {"filename": a.filename, "content_type": a.content_type}
                for a in msg.attachments
            ],
            "errors": [],
        }


def classify_message(state: EmailAgentState) -> EmailAgentState:
    provider_labels = [str(label).upper() for label in state.get("current_provider_labels", [])]
    in_spam = state.get("mailbox", "").upper() in {"SPAM", "JUNK"} or "SPAM" in provider_labels
    attachments = [
        str(item.get("filename") or item.get("content_type") or "")
        for item in state.get("attachments", [])
    ]
    result = triage_email(
        account_email=state.get("account_email", ""),
        mailbox=state.get("mailbox", ""),
        from_email=state.get("from_email", ""),
        from_name=state.get("from_name", ""),
        subject=state.get("subject", ""),
        body=state.get("normalized_text", ""),
        attachments=attachments,
        in_provider_spam=in_spam,
        is_sent_by_user=state.get("is_sent_by_user", False),
    )
    return {
        "spam_score": result.spam_score,
        "spam_reason": result.reason,
        "importance_score": result.importance_score,
        "importance_reason": result.reason,
        "priority": result.priority,  # type: ignore[typeddict-item]
        "category": result.category,
        "confidence": result.confidence,
        "action_required": result.action_required,
        "cleanup_candidate": result.cleanup_candidate,
        "cleanup_reason": result.cleanup_reason,
        "digest_summary": result.summary,
        # A classificação é local. Só regras explícitas do usuário podem sugerir
        # labels/pastas ao safety gate.
        "suggested_labels": [],
        "needs_human_review": result.needs_human_review,
        "human_review_reason": result.reason if result.needs_human_review else None,
    }


def detect_followup(state: EmailAgentState) -> EmailAgentState:
    if not state.get("is_sent_by_user"):
        return {"is_followup_waiting_response": False}
    with db_session() as session:
        msg = session.get(EmailMessage, state["db_message_id"])
        waiting, reason = detect_followup_waiting_response(session, msg)
    if not waiting:
        return {"is_followup_waiting_response": False}
    return {
        "is_followup_waiting_response": True,
        "followup_reason": reason,
        "category": "aguardando_resposta",
        "priority": "P1",
        "action_required": True,
        "cleanup_candidate": False,
        "cleanup_reason": "",
    }


_PRIORITY_RANK = {"P0": 3, "P1": 2, "P2": 1, "ignore": 0}


def apply_rules(state: EmailAgentState) -> EmailAgentState:
    """Avalia apenas regras explícitas do usuário, também pelo Ollama local."""
    with db_session() as session:
        account = session.get(EmailAccount, state["account_id"])
        rules = load_rules_for_account(session, account.email_address)
    if not rules:
        return {}
    outcomes = evaluate_rules_llm(
        account.email_address,
        state.get("subject", ""),
        state.get("from_email", ""),
        state.get("normalized_text", ""),
        rules,
    )
    if not outcomes:
        return {}

    labels = list(state.get("suggested_labels", []))
    best_priority = state.get("priority", "P2")
    reasons = []
    new_category = None
    for outcome in outcomes:
        priority = outcome.get("priority")
        if priority and _PRIORITY_RANK.get(priority, -1) > _PRIORITY_RANK.get(best_priority, -1):
            best_priority = priority
        for label in outcome.get("labels", []):
            if label not in labels:
                labels.append(label)
        if outcome.get("category"):
            new_category = outcome["category"]
        reasons.append(outcome["reason"])

    result: EmailAgentState = {
        "priority": best_priority,  # type: ignore[typeddict-item]
        "suggested_labels": labels,
        "importance_reason": "; ".join(
            filter(None, [state.get("importance_reason", ""), *reasons])
        ),
    }
    if new_category:
        result["category"] = new_category
    return result


def persist_result(state: EmailAgentState) -> EmailAgentState:
    with db_session() as session:
        msg = session.get(EmailMessage, state["db_message_id"])
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
                action_required=state.get("action_required", False),
                cleanup_candidate=state.get("cleanup_candidate", False),
                cleanup_reason=state.get("cleanup_reason"),
                digest_summary=state.get("digest_summary"),
                suggested_labels=state.get("suggested_labels"),
                model_name="ollama",
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


def run_pipeline(db_message_id: int) -> EmailAgentState:
    state: EmailAgentState = {"db_message_id": db_message_id}
    for step in (load_email, classify_message, detect_followup, apply_rules):
        state.update(step(state))
        if state.get("errors"):
            return state
    state.update(plan_safe_actions(state))
    state.update(persist_result(state))
    return state
