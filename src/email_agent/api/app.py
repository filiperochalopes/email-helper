from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import func, select, text

from email_agent.api.cleanup import router as cleanup_router
from email_agent.logging_setup import configure_logging
from email_agent.models import EmailAccount, EmailMessage, db_session

configure_logging()
app = FastAPI(title="email-helper", version="0.1.0")
app.include_router(cleanup_router)

WEB_DIR = Path(__file__).resolve().parents[1] / "web"
app.mount("/static", StaticFiles(directory=WEB_DIR / "static"), name="static")


@app.get("/", include_in_schema=False)
def web_app():
    return FileResponse(WEB_DIR / "index.html")


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
