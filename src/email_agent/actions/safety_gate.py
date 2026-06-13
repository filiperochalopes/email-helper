"""Safety gate: só ações seguras (aplicar labels AI) saem do pipeline.

Política do MVP:
- nunca deletar, nunca esvaziar lixeira, nunca mover para Spam do provedor;
- spam suspeito recebe apenas a label AI/Spam Suspeito;
- incerteza/conflito vira AI/Revisar + human_review;
- toda ação tem idempotency_key e vai para email_action_log.
"""
from typing import Any

from email_agent.actions.idempotency import already_applied, log_action, make_idempotency_key
from email_agent.intelligence.taxonomy import ALL_AI_LABELS, LABEL_REVISAR
from email_agent.logging_setup import get_logger
from email_agent.models import EmailAccount, EmailMessage, HumanReview, db_session

log = get_logger(__name__)

DESTRUCTIVE_ACTIONS = {"delete", "expunge", "empty_trash", "move_to_trash", "move_to_spam"}


def plan_safe_actions(state: dict[str, Any]) -> dict[str, Any]:
    suggested = [l for l in state.get("suggested_labels", []) if l in ALL_AI_LABELS]
    if state.get("errors"):
        suggested = sorted(set(suggested) | {LABEL_REVISAR})

    applied: list[dict[str, Any]] = []
    with db_session() as session:
        msg = session.get(EmailMessage, state["db_message_id"])
        account = session.get(EmailAccount, msg.account_id)
        current = set(msg.ai_labels or [])
        to_add = [l for l in suggested if l not in current]

        for label in to_add:
            payload = {"label": label}
            key = make_idempotency_key(msg.account_id, msg.provider_message_id, "add_label", payload)
            if already_applied(session, key):
                continue
            try:
                _apply_label(account, msg, label)
                log_action(
                    session, message_id=msg.id, action_type="add_label",
                    payload=payload, idempotency_key=key, status="success",
                )
                applied.append({"action": "add_label", "label": label})
            except Exception as exc:  # noqa: BLE001 — uma label falhar não derruba as demais
                log.error("apply_label_failed", label=label, email=msg.email_agent_id, error=str(exc))
                log_action(
                    session, message_id=msg.id, action_type="add_label",
                    payload=payload, idempotency_key=key, status="error", error=str(exc),
                )

        if state.get("needs_human_review"):
            session.add(
                HumanReview(
                    message_id=msg.id,
                    review_type="classification_uncertain",
                    prompt_text=state.get("human_review_reason"),
                    proposed_action_json={"suggested_labels": suggested},
                )
            )

    return {"applied_actions": applied}


def _apply_label(account: EmailAccount, msg: EmailMessage, label: str) -> None:
    if account.provider == "gmail_api":
        from email_agent.actions.gmail_actions import add_label as gmail_add_label

        gmail_add_label(account, msg.provider_message_id, label)
    else:
        from email_agent.actions.imap_actions import copy_to_ai_folder

        copy_to_ai_folder(account, msg, label)
