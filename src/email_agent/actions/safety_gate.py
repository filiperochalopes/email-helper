"""Safety gate: só ações seguras (organizar com labels AI) saem do pipeline.

Política do MVP:
- nunca deletar, nunca esvaziar lixeira, nunca mover para Spam/Trash do provedor;
- o único label que fica na Inbox é AI/Foco; AI/Spam Suspeito pode sair da Inbox;
- AI/Spam Suspeito só é aplicado por regra explícita ou ação do usuário;
- incerteza/conflito vira somente `human_review`, sem label no provedor;
- toda ação tem idempotency_key e vai para email_action_log.
"""
from typing import Any

from email_agent.actions.idempotency import already_applied, log_action, make_idempotency_key
from email_agent.intelligence.taxonomy import (
    ALL_AI_LABELS,
    INBOX_KEEP_LABELS,
    imap_destination,
    moves_out_of_inbox,
)
from email_agent.logging_setup import get_logger
from email_agent.models import EmailAccount, EmailMessage, HumanReview, db_session

log = get_logger(__name__)

DESTRUCTIVE_ACTIONS = {"delete", "expunge", "empty_trash", "move_to_trash", "move_to_spam"}


def plan_safe_actions(state: dict[str, Any]) -> dict[str, Any]:
    suggested = [
        label for label in state.get("suggested_labels", []) if label in ALL_AI_LABELS
    ]
    applied: list[dict[str, Any]] = []
    with db_session() as session:
        msg = session.get(EmailMessage, state["db_message_id"])
        account = session.get(EmailAccount, msg.account_id)
        current = set(msg.ai_labels or [])
        to_add = [label for label in suggested if label not in current]

        # Fica na INBOX se qualquer label sugerida representar foco.
        stays = bool(set(suggested) & INBOX_KEEP_LABELS)
        applied += _apply_to_provider(session, account, msg, to_add, suggested, stays)

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


def _apply_to_provider(
    session, account: EmailAccount, msg: EmailMessage,
    to_add: list[str], suggested: list[str], stays: bool,
) -> list[dict[str, Any]]:
    """Aplica labels e (se for o caso) move o e-mail para fora da INBOX.
    Gmail aceita múltiplas labels; IMAP escolhe UMA pasta destino por prioridade."""
    applied: list[dict[str, Any]] = []
    move_out = (not stays) and any(moves_out_of_inbox(label) for label in suggested)

    if account.provider == "gmail_api":
        from email_agent.actions.gmail_actions import add_label, remove_label

        for label in to_add:
            if not _do(session, msg, "add_label", {"label": label},
                       lambda lb=label: add_label(account, msg.provider_message_id, lb)):
                continue
            applied.append({"action": "add_label", "label": label})
        if move_out and _do(session, msg, "remove_from_inbox", {},
                            lambda: remove_label(account, msg.provider_message_id, "INBOX")):
            applied.append({"action": "remove_from_inbox"})
            msg.raw_labels = [label for label in (msg.raw_labels or []) if label != "INBOX"]
        return applied

    # IMAP: uma pasta só. Move para a de maior prioridade entre as labels que saem.
    if move_out:
        dest_label = imap_destination(suggested)
        if dest_label is not None:
            from email_agent.actions.imap_actions import move_to_ai_folder

            holder: dict[str, str] = {}

            def _move(dl=dest_label):
                holder["folder"] = move_to_ai_folder(account, msg, dl)

            if _do(session, msg, "move_to_ai_folder", {"label": dest_label}, _move):
                applied.append({"action": "move_to_ai_folder", "label": dest_label})
                if holder.get("folder"):
                    msg.mailbox = holder["folder"]
        return applied

    # Fica na INBOX: aplica a label como KEYWORD IMAP (etiqueta no lugar, não move,
    # não duplica). Best-effort — servidor sem suporte a keyword vira 'skipped'.
    applied += _apply_imap_keywords(session, account, msg, suggested)
    return applied


def _apply_imap_keywords(session, account: EmailAccount, msg: EmailMessage,
                         suggested: list[str]) -> list[dict[str, Any]]:
    from email_agent.actions.imap_actions import add_keyword

    applied: list[dict[str, Any]] = []
    for label in [candidate for candidate in suggested if candidate in INBOX_KEEP_LABELS]:
        payload = {"label": label}
        key = make_idempotency_key(msg.account_id, msg.provider_message_id, "add_keyword", payload)
        if already_applied(session, key):
            continue
        try:
            ok = add_keyword(account, msg, label)
            log_action(session, message_id=msg.id, action_type="add_keyword",
                       payload=payload, idempotency_key=key,
                       status="success" if ok else "skipped",
                       error=None if ok else "servidor IMAP sem suporte a keyword")
            if ok:
                applied.append({"action": "add_keyword", "label": label})
        except Exception as exc:  # noqa: BLE001 — uma ação falhar não derruba as demais
            log.error("add_keyword_failed", label=label, email=msg.email_agent_id, error=str(exc))
            log_action(session, message_id=msg.id, action_type="add_keyword",
                       payload=payload, idempotency_key=key, status="error", error=str(exc))
    return applied


def _do(session, msg: EmailMessage, action_type: str, payload: dict, fn) -> bool:
    """Executa fn() com idempotência + log. Retorna True se executou (ou pulou por já
    aplicada), False em erro. Uma ação falhar não derruba as demais."""
    key = make_idempotency_key(msg.account_id, msg.provider_message_id, action_type, payload)
    if already_applied(session, key):
        return False
    try:
        fn()
        log_action(session, message_id=msg.id, action_type=action_type,
                   payload=payload, idempotency_key=key, status="success")
        return True
    except Exception as exc:  # noqa: BLE001 — uma ação falhar não derruba as demais
        log.error("safe_action_failed", action=action_type, email=msg.email_agent_id, error=str(exc))
        log_action(session, message_id=msg.id, action_type=action_type,
                   payload=payload, idempotency_key=key, status="error", error=str(exc))
        return False


def apply_label(account: EmailAccount, msg: EmailMessage, label: str) -> None:
    """Aplica UMA label no provedor respeitando a política move/stay. Usado pelo
    comando CLI `label` (manual). Não registra em email_action_log (caminho do usuário)."""
    if account.provider == "gmail_api":
        from email_agent.actions.gmail_actions import add_label, move_to_label

        if moves_out_of_inbox(label):
            move_to_label(account, msg.provider_message_id, label)
        else:
            add_label(account, msg.provider_message_id, label)
    elif moves_out_of_inbox(label):
        from email_agent.actions.imap_actions import move_to_ai_folder

        msg.mailbox = move_to_ai_folder(account, msg, label)
    else:
        # IMAP + label que fica na INBOX: keyword (etiqueta no lugar, não duplica).
        from email_agent.actions.imap_actions import add_keyword

        add_keyword(account, msg, label)


# Compat: nome antigo usado pelo CLI.
_apply_label = apply_label
