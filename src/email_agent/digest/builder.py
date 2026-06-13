"""Geração do resumo matinal para WhatsApp.

Produz até 3 mensagens (curtas, com formatação WhatsApp e emojis):
  - main:  TL;DR acionável de TODAS as caixas — só P0; se não houver P0, lista P1.
  - news:  📰 compilado de notícias/novidades (newsletters de conteúdo).
  - promo: 🏷️ promoções.
WhatsApp markdown: *negrito*, _itálico_, ~tachado~, `mono`.
"""
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import func, select

from email_agent.intelligence.taxonomy import LABEL_AGUARDANDO, LABEL_FISCAL, LABEL_REVISAR
from email_agent.models import (
    DailyDigest,
    EmailAccount,
    EmailClassification,
    EmailMessage,
    db_session,
)

# Cap do TL;DR principal (~500 tokens ≈ 2000 chars)
MAIN_MAX_CHARS = 2000
MAX_ACTION_ITEMS = 6
MAX_WAITING = 4
MAX_NEWS = 12
MAX_PROMO = 10


@dataclass
class Digest:
    main: str
    news: str | None = None
    promo: str | None = None

    def messages(self) -> list[str]:
        return [m for m in (self.main, self.news, self.promo) if m]


@dataclass
class _Stats:
    total: int = 0
    fiscais: int = 0
    docs: int = 0
    spam: int = 0
    revisar: int = 0
    contas: int = 0
    reauth: int = 0
    erros: list[str] = field(default_factory=list)


def _short(s: str | None, n: int) -> str:
    s = (s or "").strip().replace("\n", " ")
    return s if len(s) <= n else s[: n - 1] + "…"


def _sender(m: EmailMessage) -> str:
    return _short(m.from_name or (m.from_email or "").split("@")[0], 28)


def _item(m: EmailMessage, c: EmailClassification, *, action: bool) -> str:
    line = f"• *{_sender(m)}* — {_short(m.subject, 60)}"
    detail = _short(c.digest_summary or m.snippet, 90)
    if detail:
        verb = "_ação:_" if action else "_resumo:_"
        line += f"\n  {verb} {detail}"
    line += f" `{m.email_agent_id}`"
    return line


def build_digest(for_date: date | None = None) -> Digest:
    for_date = for_date or date.today()
    since = datetime.now(timezone.utc) - timedelta(hours=24)

    with db_session() as session:
        rows = session.execute(
            select(EmailMessage, EmailClassification)
            .join(EmailClassification, EmailClassification.message_id == EmailMessage.id)
            .where(EmailMessage.created_at >= since)
            .order_by(EmailClassification.importance_score.desc())
        ).all()
        # uma classificação por mensagem (a mais recente)
        latest: dict[int, tuple] = {}
        for m, c in rows:
            if m.id not in latest or c.id > latest[m.id][1].id:
                latest[m.id] = (m, c)
        rows = sorted(latest.values(), key=lambda mc: -(mc[1].importance_score or 0))

        waiting = [(m, c) for m, c in rows if LABEL_AGUARDANDO in (m.ai_labels or [])][:MAX_WAITING]
        waiting_ids = {m.id for m, _ in waiting}
        p0 = [(m, c) for m, c in rows if c.priority == "P0" and m.id not in waiting_ids]
        p1 = [(m, c) for m, c in rows if c.priority == "P1" and m.id not in waiting_ids]
        news = [(m, c) for m, c in rows if c.category == "noticia"][:MAX_NEWS]
        promo = [(m, c) for m, c in rows if c.category == "promocao"][:MAX_PROMO]

        st = _Stats(
            total=len(rows),
            fiscais=sum(1 for m, _ in rows if LABEL_FISCAL in (m.ai_labels or [])),
            docs=sum(1 for _, c in rows if c.category in ("documento", "documento_fiscal")),
            spam=sum(1 for _, c in rows if c.category == "spam_suspeito"),
            revisar=sum(1 for m, _ in rows if LABEL_REVISAR in (m.ai_labels or [])),
            contas=session.execute(
                select(func.count()).select_from(EmailAccount).where(EmailAccount.is_active.is_(True))
            ).scalar(),
            reauth=session.execute(
                select(func.count()).select_from(EmailAccount).where(EmailAccount.auth_status != "ok")
            ).scalar(),
        )

        main = _build_main(for_date, p0, p1, waiting, st)
        news_msg = _build_news(news) if news else None
        promo_msg = _build_promo(promo) if promo else None

    return Digest(main=main, news=news_msg, promo=promo_msg)


def _build_main(for_date, p0, p1, waiting, st: _Stats) -> str:
    action = p0 if p0 else p1
    n_action = len(action) + len(waiting)
    out = [
        f"🌅 *Resumo matinal* — {for_date:%d/%m}",
        f"_{st.contas} caixas · {st.total} e-mails · {n_action} pedem atenção_",
    ]
    if action:
        head = "🔴 *Precisa de ação hoje* (P0)" if p0 else "🟡 *Importante* (sem P0 hoje)"
        out += ["", head]
        out += [_item(m, c, action=True) for m, c in action[:MAX_ACTION_ITEMS]]
        if len(action) > MAX_ACTION_ITEMS:
            out.append(f"_…+{len(action) - MAX_ACTION_ITEMS} no app_")
    else:
        out += ["", "✅ Nada urgente."]

    if waiting:
        out += ["", f"⏳ *Aguardando resposta* ({len(waiting)})"]
        out += [f"• *{_sender(m)}* — {_short(m.subject, 50)} `{m.email_agent_id}`" for m, _ in waiting]

    out += ["", f"📊 {st.docs} docs · {st.fiscais} fiscais · {st.spam} spam · {st.revisar} p/ revisar"]
    if st.reauth:
        out.append(f"⚠️ {st.reauth} conta(s) p/ reautenticar: `email-agent accounts list`")

    text = "\n".join(out)
    if len(text) > MAIN_MAX_CHARS:
        text = text[: MAIN_MAX_CHARS - 1] + "…"
    return text


def _build_news(news) -> str:
    out = [f"📰 *Novidades de hoje* ({len(news)})"]
    out += [f"• *{_sender(m)}* — {_short(m.subject, 70)} `{m.email_agent_id}`" for m, _ in news]
    out += ["", "_Não quer receber um tipo? `email-agent feedback <ID>` → opção notícia 👎_"]
    return "\n".join(out)


def _build_promo(promo) -> str:
    out = [f"🏷️ *Promoções* ({len(promo)})"]
    out += [f"• *{_sender(m)}* — {_short(m.subject, 70)} `{m.email_agent_id}`" for m, _ in promo]
    return "\n".join(out)


def save_digest(body: str, sent_to: str, status: str) -> None:
    with db_session() as session:
        session.add(
            DailyDigest(
                digest_date=date.today(),
                sent_to=sent_to,
                body=body,
                sent_at=datetime.now(timezone.utc) if status == "sent" else None,
                status=status,
            )
        )
