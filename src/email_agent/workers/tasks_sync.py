from sqlalchemy import select

from email_agent.logging_setup import get_logger
from email_agent.models import EmailAccount, db_session
from email_agent.workers.celery_app import app

log = get_logger(__name__)


@app.task(name="email_agent.sync_all_accounts")
def sync_all_accounts(bootstrap: bool = False) -> dict:
    """Sincroniza todas as contas ativas. Falha em uma conta não interrompe as demais."""
    with db_session() as session:
        accounts = (
            session.execute(select(EmailAccount).where(EmailAccount.is_active.is_(True)))
            .scalars()
            .all()
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


def sync_one_account(account_id: int, provider: str, bootstrap: bool) -> dict:
    if provider == "gmail_api":
        from email_agent.sync.gmail_sync import sync_account
    else:
        from email_agent.sync.imap_sync import sync_account
    new_ids = sync_account(account_id, bootstrap=bootstrap)
    # Enfileira classificação imediata das novas
    from email_agent.workers.tasks_classify import classify_message

    for db_id in new_ids:
        classify_message.delay(db_id)
    return {"new_messages": len(new_ids)}


@app.task(name="email_agent.sync_account")
def sync_account_task(account_id: int, bootstrap: bool = False) -> dict:
    with db_session() as session:
        account = session.get(EmailAccount, account_id)
        provider = account.provider
    return sync_one_account(account_id, provider, bootstrap)
