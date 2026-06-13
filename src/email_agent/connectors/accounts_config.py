"""Carrega a declaração de contas de secrets/accounts.yml e sincroniza com o banco."""
import os

import yaml
from sqlalchemy import select

from email_agent.logging_setup import get_logger
from email_agent.models import EmailAccount, db_session

log = get_logger(__name__)

ACCOUNTS_FILE = os.environ.get("ACCOUNTS_FILE", "/secrets/accounts.yml")


def load_accounts_yaml(path: str | None = None) -> dict:
    path = path or ACCOUNTS_FILE
    if not os.path.exists(path):
        raise FileNotFoundError(
            f"{path} não encontrado. Copie secrets/accounts.example.yml para secrets/accounts.yml."
        )
    with open(path) as f:
        return yaml.safe_load(f) or {}


def imap_credentials(email_address: str, path: str | None = None) -> dict:
    data = load_accounts_yaml(path)
    for entry in data.get("imap", []):
        if entry.get("email") == email_address:
            return entry
    raise KeyError(f"Conta IMAP não declarada em accounts.yml: {email_address}")


def import_accounts(path: str | None = None) -> dict[str, int]:
    """Upsert das contas declaradas no YAML. Não remove contas do banco;
    contas ausentes do YAML são apenas desativadas."""
    data = load_accounts_yaml(path)
    declared: set[str] = set()
    created = updated = 0

    with db_session() as session:
        for provider, entries in (("gmail_api", data.get("gmail", [])), ("imap", data.get("imap", []))):
            for entry in entries:
                email = entry["email"]
                declared.add(email)
                acc = session.execute(
                    select(EmailAccount).where(EmailAccount.email_address == email)
                ).scalar_one_or_none()
                if acc is None:
                    acc = EmailAccount(provider=provider, email_address=email)
                    session.add(acc)
                    created += 1
                else:
                    updated += 1
                acc.provider = provider
                acc.display_name = entry.get("display_name")
                acc.is_active = entry.get("active", True)
                if provider == "imap":
                    acc.imap_host = entry.get("host")
                    acc.imap_port = entry.get("port", 993)

        deactivated = 0
        for acc in session.execute(select(EmailAccount)).scalars():
            if acc.email_address not in declared and acc.is_active:
                acc.is_active = False
                deactivated += 1

    log.info("accounts_imported", created=created, updated=updated, deactivated=deactivated)
    return {"created": created, "updated": updated, "deactivated": deactivated}
