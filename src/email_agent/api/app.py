from fastapi import FastAPI
from sqlalchemy import func, select, text

from email_agent.logging_setup import configure_logging
from email_agent.models import EmailAccount, EmailMessage, db_session

configure_logging()
app = FastAPI(title="email-agent", version="0.1.0")


@app.get("/health")
def health():
    with db_session() as session:
        session.execute(text("SELECT 1"))
    return {"status": "ok"}


@app.get("/admin/status")
def admin_status():
    with db_session() as session:
        accounts = session.execute(select(EmailAccount)).scalars().all()
        total = session.execute(select(func.count()).select_from(EmailMessage)).scalar()
        return {
            "messages": total,
            "accounts": [
                {
                    "email": a.email_address,
                    "provider": a.provider,
                    "active": a.is_active,
                    "auth_status": a.auth_status,
                }
                for a in accounts
            ],
        }
