"""Cliente Gmail API com OAuth2 offline e marcação de reauth_required.

Tokens ficam em GMAIL_TOKEN_STORAGE_PATH/<email>.json. A autorização inicial
é feita FORA do Docker (precisa de navegador):  email-agent gmail auth CONTA
"""
import os

from google.auth.exceptions import RefreshError
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

from email_agent.config import get_settings
from email_agent.logging_setup import get_logger
from email_agent.models import EmailAccount, db_session

log = get_logger(__name__)

SCOPES = ["https://www.googleapis.com/auth/gmail.modify"]


def token_path(email_address: str) -> str:
    return os.path.join(get_settings().gmail_token_storage_path, f"{email_address}.json")


def run_oauth_flow(email_address: str) -> None:
    """Fluxo interativo (navegador). Rodar no host, não no container."""
    settings = get_settings()
    flow = InstalledAppFlow.from_client_secrets_file(settings.gmail_oauth_client_secret_file, SCOPES)
    creds = flow.run_local_server(port=0, prompt="consent", access_type="offline")
    os.makedirs(settings.gmail_token_storage_path, exist_ok=True)
    with open(token_path(email_address), "w") as f:
        f.write(creds.to_json())
    log.info("gmail_oauth_ok", account=email_address)


def get_credentials(account: EmailAccount) -> Credentials | None:
    path = token_path(account.email_address)
    if not os.path.exists(path):
        _mark_reauth(account)
        return None
    creds = Credentials.from_authorized_user_file(path, SCOPES)
    if creds.valid:
        return creds
    if creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
            with open(path, "w") as f:
                f.write(creds.to_json())
            return creds
        except RefreshError as exc:
            log.error("gmail_refresh_failed", account=account.email_address, error=str(exc))
            _mark_reauth(account)
            return None
    _mark_reauth(account)
    return None


def _mark_reauth(account: EmailAccount) -> None:
    with db_session() as session:
        db_account = session.get(EmailAccount, account.id)
        if db_account:
            db_account.auth_status = "reauth_required"
    log.warning("gmail_reauth_required", account=account.email_address)


def get_service(account: EmailAccount):
    creds = get_credentials(account)
    if creds is None:
        raise RuntimeError(f"Conta {account.email_address} precisa de reautenticação OAuth")
    return build("gmail", "v1", credentials=creds, cache_discovery=False)
