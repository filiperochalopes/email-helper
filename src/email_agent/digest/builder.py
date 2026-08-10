"""Geração do resumo matinal para WhatsApp.

Produz até 4 mensagens (curtas, com formatação WhatsApp e emojis):
  - main:    TL;DR acionável de TODAS as caixas — só P0; se não houver P0, lista P1.
  - news:    📰 compilado de notícias/novidades (newsletters de conteúdo).
  - promo:   🏷️ promoções.
  - cleanup: 🧹 candidatos conservadores sugeridos pela LLM para revisão humana.
WhatsApp markdown: *negrito*, _itálico_, ~tachado~, `mono`.

E-mails mais antigos que `digest_max_age_days` não entram no resumo.
"""
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import func, or_, select

from email_agent.config import get_settings
from email_agent.intelligence.prioritizer import prioritize_p0
from email_agent.logging_setup import get_logger
from email_agent.models import (
    DailyDigest,
    EmailAccount,
    EmailClassification,
    EmailMessage,
    db_session,
)

log = get_logger(__name__)

# Cap do TL;DR principal (~500 tokens ≈ 2000 chars)
MAIN_MAX_CHARS = 2000
MAX_ACTION_ITEMS = 6
MAX_WAITING = 4
MAX_NEWS = 12
MAX_PROMO = 10
MAX_CLEANUP = 15
# Acima deste nº de itens acionáveis, o agente de priorização (modelo reasoning) reordena.
PRIORITIZE_THRESHOLD = 5

# Prefixo das dicas de investigação no rodapé das mensagens.
DOCKER = "docker compose exec -it app email-agent"


@dataclass
class Digest:
    main: str
    news: str | None = None
    promo: str | None = None
    cleanup: str | None = None

    def messages(self) -> list[str]:
        return [m for m in (self.main, self.news, self.promo, self.cleanup) if m]


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
    now = datetime.now(UTC)
    for_date = for_date or now.date()
    since = now - timedelta(hours=24)
    age_cutoff = now - timedelta(days=get_settings().digest_max_age_days)

    with db_session() as session:
        rows = session.execute(
            select(EmailMessage, EmailClassification)
            .join(EmailClassification, EmailClassification.message_id == EmailMessage.id)
            .where(EmailMessage.created_at >= since)
            # e-mails antigos (data > N meses) não entram no resumo
            .where(or_(EmailMessage.date.is_(None), EmailMessage.date >= age_cutoff))
            .order_by(EmailClassification.importance_score.desc())
        ).all()
        # uma classificação por mensagem (a mais recente)
        latest: dict[int, tuple] = {}
        for m, c in rows:
            if m.id not in latest or c.id > latest[m.id][1].id:
                latest[m.id] = (m, c)
        rows = sorted(latest.values(), key=lambda mc: -(mc[1].importance_score or 0))

        waiting = [(m, c) for m, c in rows if c.category == "aguardando_resposta"][:MAX_WAITING]
        waiting_ids = {m.id for m, _ in waiting}
        p0 = [(m, c) for m, c in rows if c.priority == "P0" and m.id not in waiting_ids]
        p1 = [(m, c) for m, c in rows if c.priority == "P1" and m.id not in waiting_ids]
        news = [(m, c) for m, c in rows if c.category == "noticia"][:MAX_NEWS]
        promo = [(m, c) for m, c in rows if c.category == "promocao"][:MAX_PROMO]

        # Muitos urgentes: reordena por urgência real com o modelo reasoning.
        if len(p0) > PRIORITIZE_THRESHOLD:
            p0 = prioritize_p0(p0)

        cleanup = _cleanup_candidates(session, age_cutoff=now - timedelta(
            days=get_settings().cleanup_min_age_days
        ))

        st = _Stats(
            total=len(rows),
            fiscais=sum(1 for _, c in rows if c.category == "documento_fiscal"),
            docs=sum(1 for _, c in rows if c.category in ("documento", "documento_fiscal")),
            spam=sum(1 for _, c in rows if c.category == "spam_suspeito"),
            revisar=sum(1 for _, c in rows if c.category == "revisar"),
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
        cleanup_msg = _build_cleanup(cleanup) if cleanup else None

    return Digest(main=main, news=news_msg, promo=promo_msg, cleanup=cleanup_msg)


def _cleanup_candidates(session, *, age_cutoff: datetime) -> list[tuple]:
    """Sugestões conservadoras da LLM; não altera o provedor nem apaga nada."""
    rows = session.execute(
        select(EmailMessage, EmailClassification)
        .join(EmailClassification, EmailClassification.message_id == EmailMessage.id)
        .where(EmailClassification.cleanup_candidate.is_(True))
        .where(EmailMessage.date.is_not(None), EmailMessage.date < age_cutoff)
        .where(~EmailMessage.mailbox.ilike("%trash%"), ~EmailMessage.mailbox.ilike("%lixeira%"))
        .order_by(EmailMessage.date.asc())  # mais antigos primeiro
        .limit(MAX_CLEANUP)
    ).all()
    seen: set[int] = set()
    out = []
    for m, c in rows:
        if m.id in seen:
            continue
        seen.add(m.id)
        out.append((m, c))
    return out


def _ids(pairs) -> str:
    return " ".join(m.email_agent_id for m, _ in pairs)


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
        extra = action[MAX_ACTION_ITEMS:]
        if extra:
            out.append(f"_…+{len(extra)} no app:_ {_ids(extra)}")
        # Lembrete: ver o conteúdo completo de qualquer item urgente.
        out += ["", f"_Abrir um item: `{DOCKER} show <ID>` (aceita vários IDs)_"]
    else:
        out += ["", "✅ Nada urgente."]

    if waiting:
        out += ["", f"⏳ *Aguardando resposta* ({len(waiting)})"]
        out += [f"• *{_sender(m)}* — {_short(m.subject, 50)} `{m.email_agent_id}`" for m, _ in waiting]

    out += ["", f"📊 {st.docs} docs · {st.fiscais} fiscais · {st.spam} spam · {st.revisar} p/ revisar"]
    if st.reauth:
        out.append(f"⚠️ {st.reauth} conta(s) p/ reautenticar: `{DOCKER} accounts list`")

    text = "\n".join(out)
    if len(text) > MAIN_MAX_CHARS:
        text = text[: MAIN_MAX_CHARS - 1] + "…"
    return text


def _build_news(news) -> str:
    out = [f"📰 *Novidades de hoje* ({len(news)})"]
    out += [f"• *{_sender(m)}* — {_short(m.subject, 70)} `{m.email_agent_id}`" for m, _ in news]
    out += [
        "",
        f"_Não quer receber um tipo? `{DOCKER} feedback <ID>` → opção notícia 👎_",
        f"_Ler na íntegra: `{DOCKER} show <ID>`_",
    ]
    return "\n".join(out)


def _build_promo(promo) -> str:
    out = [f"🏷️ *Promoções* ({len(promo)})"]
    out += [f"• *{_sender(m)}* — {_short(m.subject, 70)} `{m.email_agent_id}`" for m, _ in promo]
    out += ["", f"_Detalhes: `{DOCKER} show <ID>`_"]
    return "\n".join(out)


def _build_cleanup(cleanup) -> str:
    out = [f"🧹 *Candidatos a exclusão* ({len(cleanup)})", "_Antigos e sem valor de arquivamento._"]
    out += [
        f"• *{_sender(m)}* — {_short(m.subject, 60)} `{m.email_agent_id}`" for m, _ in cleanup
    ]
    out += [
        "",
        f"_Revise e apague (vai p/ Lixeira, confirma 1 a 1): `{DOCKER} delete {_ids(cleanup)}`_",
        f"_Ver antes de apagar: `{DOCKER} show <ID>`_",
    ]
    return "\n".join(out)


def save_digest(body: str, sent_to: str, status: str) -> None:
    with db_session() as session:
        session.add(
            DailyDigest(
                digest_date=datetime.now(UTC).date(),
                sent_to=sent_to,
                body=body,
                sent_at=datetime.now(UTC) if status == "sent" else None,
                status=status,
            )
        )
