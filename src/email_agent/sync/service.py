"""Orquestração síncrona e leve de sync + triagem, sem broker ou workers."""
from sqlalchemy import select

from email_agent.intelligence.graph import run_pipeline
from email_agent.logging_setup import get_logger
from email_agent.models import EmailAccount, EmailClassification, EmailMessage, db_session

log = get_logger(__name__)


def classify_message(db_message_id: int) -> dict:
    with db_session() as session:
        msg = session.get(EmailMessage, db_message_id)
        if msg and "TRASH" in (msg.raw_labels or []):
            return {"skipped": "trash"}
    state = run_pipeline(db_message_id)
    return {
        "email_agent_id": state.get("email_agent_id"),
        "category": state.get("category"),
        "priority": state.get("priority"),
        "cleanup_candidate": state.get("cleanup_candidate", False),
    }


def classify_pending(limit: int = 2000) -> dict:
    with db_session() as session:
        classified = select(EmailClassification.message_id)
        pending = list(
            session.execute(
                select(EmailMessage.id)
                .where(EmailMessage.id.not_in(classified))
                .order_by(EmailMessage.id.desc())
                .limit(limit)
            ).scalars()
        )
    done = errors = 0
    for db_id in pending:
        try:
            classify_message(db_id)
            done += 1
        except Exception as exc:  # noqa: BLE001
            errors += 1
            log.error("message_triage_failed", db_message_id=db_id, error=str(exc))
    return {"classified": done, "errors": errors}


def reclassify_legacy_inbox(limit: int = 40) -> dict:
    """Atualiza em lote classificações antigas, sem ação automática no provedor."""
    with db_session() as session:
        message_ids = list(
            session.execute(
                select(EmailMessage.id)
                .join(
                    EmailClassification,
                    EmailClassification.message_id == EmailMessage.id,
                )
                .where(
                    EmailMessage.mailbox == "INBOX",
                    EmailMessage.is_sent_by_user.is_(False),
                    EmailClassification.llm_provider == "legacy",
                )
                .order_by(EmailMessage.date.desc().nullslast())
                .limit(max(1, min(limit, 500)))
            ).scalars()
        )
    done = errors = candidates = 0
    for db_id in message_ids:
        try:
            result = classify_message(db_id)
            done += 1
            candidates += int(result.get("cleanup_candidate", False))
        except Exception as exc:  # noqa: BLE001
            errors += 1
            log.error("legacy_retriage_failed", db_message_id=db_id, error=str(exc))
    return {
        "reclassified": done,
        "cleanup_candidates": candidates,
        "errors": errors,
    }


def sync_one_account(account_id: int, provider: str, bootstrap: bool) -> dict:
    if provider == "gmail_api":
        from email_agent.sync.gmail_sync import sync_account
    else:
        from email_agent.sync.imap_sync import sync_account
    new_ids = sync_account(account_id, bootstrap=bootstrap)
    classified = errors = 0
    for db_id in new_ids:
        try:
            classify_message(db_id)
            classified += 1
        except Exception as exc:  # noqa: BLE001
            errors += 1
            log.error("message_triage_failed", db_message_id=db_id, error=str(exc))
    return {"new_messages": len(new_ids), "classified": classified, "errors": errors}


def sync_all_accounts(bootstrap: bool = False) -> dict:
    """Sincroniza contas em sequência; a falha de uma nunca interrompe as outras."""
    with db_session() as session:
        accounts = list(
            session.execute(
                select(EmailAccount).where(EmailAccount.is_active.is_(True))
            ).scalars()
        )
        plan = [(a.id, a.provider, a.email_address) for a in accounts]

    results = {}
    for account_id, provider, email_address in plan:
        try:
            results[email_address] = sync_one_account(account_id, provider, bootstrap)
        except Exception as exc:  # noqa: BLE001
            log.error("account_sync_failed", account=email_address, error=str(exc))
            results[email_address] = {"error": str(exc)}
    return results
