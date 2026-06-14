"""CLI administrativa do email-agent (Typer + Rich).

Correção pontual:  email-agent feedback E-...  |  email-agent label E-...
"""
import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from sqlalchemy import select

from email_agent.intelligence.taxonomy import ALL_AI_LABELS
from email_agent.logging_setup import configure_logging
from email_agent.models import (
    EmailAccount,
    EmailClassification,
    EmailMessage,
    EmailTrainingEvent,
    EmailUserEvent,
    db_session,
)

app = typer.Typer(help="email-agent: monitoramento e classificação de e-mails")
console = Console()

sync_app = typer.Typer(help="Sincronização")
relabel_app = typer.Typer(help="Relabel/bootstrap")
review_app = typer.Typer(help="Revisão (Label Studio)")
train_app = typer.Typer(help="Treinamento")
accounts_app = typer.Typer(help="Contas")
gmail_app = typer.Typer(help="Gmail OAuth")
rules_app = typer.Typer(help="Regras de importância (LLM)")
app.add_typer(sync_app, name="sync")
app.add_typer(relabel_app, name="relabel")
app.add_typer(review_app, name="review")
app.add_typer(train_app, name="train")
app.add_typer(accounts_app, name="accounts")
app.add_typer(gmail_app, name="gmail")
app.add_typer(rules_app, name="rules")


@app.callback()
def _init():
    configure_logging()


# ---------- helpers ----------

def _load_message(email_agent_id: str) -> tuple[EmailMessage, EmailClassification | None]:
    with db_session() as session:
        msg = session.execute(
            select(EmailMessage).where(EmailMessage.email_agent_id == email_agent_id)
        ).scalar_one_or_none()
        if msg is None:
            console.print(f"[red]Mensagem {email_agent_id} não encontrada.[/red]")
            raise typer.Exit(1)
        cls = session.execute(
            select(EmailClassification)
            .where(EmailClassification.message_id == msg.id)
            .order_by(EmailClassification.id.desc())
            .limit(1)
        ).scalar_one_or_none()
        _ = msg.attachments  # carrega antes de fechar a sessão
        return msg, cls


def _print_summary(msg: EmailMessage, cls: EmailClassification | None) -> None:
    with db_session() as session:
        account = session.get(EmailAccount, msg.account_id)
    t = Table(show_header=False, box=None)
    t.add_row("ID", msg.email_agent_id)
    t.add_row("Conta", account.email_address if account else str(msg.account_id))
    t.add_row("De", f"{msg.from_name or ''} <{msg.from_email or ''}>")
    t.add_row("Assunto", msg.subject or "(sem assunto)")
    t.add_row("Data", str(msg.date))
    t.add_row("Pasta", msg.mailbox)
    t.add_row("Labels AI", ", ".join(msg.ai_labels or []) or "—")
    if cls:
        t.add_row("Prioridade", cls.priority or "—")
        t.add_row("Categoria sugerida", cls.category or "—")
        t.add_row("Resumo", cls.digest_summary or (msg.snippet or "")[:160])
        t.add_row("Motivo", (cls.importance_reason or "")[:200])
    console.print(t)
    console.print()


FEEDBACK_OPTIONS = [
    ("spam_suspeito", "Spam suspeito — phishing, cobrança indevida, currículo não solicitado, anexo suspeito."),
    ("ham", "Não é spam — falso positivo; mensagem legítima."),
    ("marketing", "Marketing — loja, newsletter, promoção ou rastreio sem ação."),
    ("noticia", "Notícia — newsletter/conteúdo que me interessa (nova série, artigo, novidade)."),
    ("promocao", "Promoção — desconto, cupom, oferta."),
    ("documento", "Documento — possui documento/anexo relevante."),
    ("documento_fiscal", "Documento fiscal — nota fiscal, boleto, fatura, cobrança, DAS, INSS, recibo."),
    ("aguardando_resposta", "Aguardando resposta — follow-up que preciso acompanhar."),
    ("importante_p0", "Importante P0 — exige ação hoje ou risco alto se ignorado."),
    ("importante_p1", "Importante P1 — importante, mas sem urgência imediata."),
    ("ignorar", "Ignorar — não relevante ou já resolvido."),
    ("revisar", "Revisar — deixar em fila de dúvida/curadoria."),
]


@app.command()
def feedback(email_agent_id: str):
    """Feedback explícito com menu interativo (treina a classificação)."""
    msg, cls = _load_message(email_agent_id)
    _print_summary(msg, cls)
    for i, (_, desc) in enumerate(FEEDBACK_OPTIONS, start=1):
        console.print(f"{i:>2}. {desc}")
    choice = typer.prompt("\nEscolha uma opção", type=int)
    if not 1 <= choice <= len(FEEDBACK_OPTIONS):
        console.print("[red]Opção inválida.[/red]")
        raise typer.Exit(1)
    label, desc = FEEDBACK_OPTIONS[choice - 1]

    with db_session() as session:
        session.add(
            EmailTrainingEvent(
                message_id=msg.id,
                label=label,
                source="explicit_cli_feedback",
                weight=1.0,
                trusted=True,
                reason=desc,
            )
        )
    console.print(f"[green]Feedback registrado:[/green] {label}")

    from email_agent.intelligence.taxonomy import CATEGORY_TO_LABELS

    labels = CATEGORY_TO_LABELS.get(label, [])
    if labels and typer.confirm("Aplicar label/pasta correspondente agora?", default=False):
        for lb in labels:
            _apply_label_to_provider(msg, lb)
        console.print(f"[green]Labels aplicadas:[/green] {', '.join(labels)}")


@app.command()
def label(email_agent_id: str):
    """Aplicar/remover labels AI com menu interativo (altera o provedor)."""
    msg, cls = _load_message(email_agent_id)
    _print_summary(msg, cls)
    for i, lb in enumerate(ALL_AI_LABELS, start=1):
        console.print(f"{i}. {lb}")
    n = len(ALL_AI_LABELS)
    console.print(f"{n + 1}. Remover todas labels AI desta mensagem.")
    console.print(f"{n + 2}. Cancelar.")
    choice = typer.prompt("\nEscolha uma opção", type=int)

    if choice == n + 2:
        raise typer.Exit()
    if choice == n + 1:
        for lb in list(msg.ai_labels or []):
            _remove_label_from_provider(msg, lb)
        _record_label_event(msg, previous=msg.ai_labels or [], new=[], event="removed_label")
        console.print("[green]Labels AI removidas.[/green]")
        return
    if not 1 <= choice <= n:
        console.print("[red]Opção inválida.[/red]")
        raise typer.Exit(1)

    lb = ALL_AI_LABELS[choice - 1]
    _apply_label_to_provider(msg, lb)
    _record_label_event(
        msg, previous=msg.ai_labels or [], new=sorted(set(msg.ai_labels or []) | {lb}),
        event="added_label",
    )
    console.print(f"[green]Label aplicada:[/green] {lb}")


def _apply_label_to_provider(msg: EmailMessage, lb: str) -> None:
    from email_agent.actions.safety_gate import _apply_label

    with db_session() as session:
        account = session.get(EmailAccount, msg.account_id)
        db_msg = session.get(EmailMessage, msg.id)
        _apply_label(account, db_msg, lb)
        db_msg.ai_labels = sorted(set(db_msg.ai_labels or []) | {lb})


def _remove_label_from_provider(msg: EmailMessage, lb: str) -> None:
    with db_session() as session:
        account = session.get(EmailAccount, msg.account_id)
        db_msg = session.get(EmailMessage, msg.id)
        if account.provider == "gmail_api":
            from email_agent.actions.gmail_actions import remove_label

            remove_label(account, db_msg.provider_message_id, lb)
        db_msg.ai_labels = [x for x in (db_msg.ai_labels or []) if x != lb]


def _record_label_event(msg: EmailMessage, previous: list, new: list, event: str) -> None:
    with db_session() as session:
        session.add(
            EmailUserEvent(
                message_id=msg.id,
                event_type=event,
                previous_labels=previous,
                new_labels=new,
                source="cli",
            )
        )


@app.command()
def search(
    label: str = typer.Option(None, "--label"),
    from_: str = typer.Option(None, "--from"),
    subject: str = typer.Option(None, "--subject"),
    category: str = typer.Option(None, "--category"),
    priority: str = typer.Option(None, "--priority"),
    limit: int = typer.Option(20, "--limit"),
):
    """Busca mensagens para descobrir IDs."""
    with db_session() as session:
        q = (
            select(EmailMessage, EmailClassification)
            .outerjoin(EmailClassification, EmailClassification.message_id == EmailMessage.id)
            .order_by(EmailMessage.id.desc())
            .limit(limit * 5)
        )
        if from_:
            q = q.where(EmailMessage.from_email.ilike(f"%{from_}%"))
        if subject:
            q = q.where(EmailMessage.subject.ilike(f"%{subject}%"))
        if category:
            q = q.where(EmailClassification.category == category)
        if priority:
            q = q.where(EmailClassification.priority == priority)
        rows = session.execute(q).all()

    t = Table()
    for col in ("ID", "De", "Assunto", "Categoria", "Prio", "Labels"):
        t.add_column(col)
    shown = 0
    for msg, cls in rows:
        if label and label not in (msg.ai_labels or []):
            continue
        t.add_row(
            msg.email_agent_id, msg.from_email or "", (msg.subject or "")[:50],
            cls.category if cls else "—", cls.priority if cls else "—",
            ", ".join(msg.ai_labels or []),
        )
        shown += 1
        if shown >= limit:
            break
    console.print(t)


@app.command()
def show(
    email_agent_ids: list[str] = typer.Argument(..., help="Um ou mais IDs (E-YYYYMMDD-NNNNNN)."),
):
    """Mostra detalhes de uma ou mais mensagens."""
    for i, eid in enumerate(email_agent_ids):
        if i:
            console.rule(style="dim")
        msg, cls = _load_message(eid)
        _print_summary(msg, cls)
        console.print((msg.normalized_text or "")[:2000])


def _delete_impl(email_agent_ids: list[str]) -> None:
    from email_agent.actions.delete_actions import trash_message

    for eid in email_agent_ids:
        msg, cls = _load_message(eid)
        _print_summary(msg, cls)
        body = (msg.normalized_text or msg.snippet or "(sem corpo)").strip()
        console.print(Panel(body[:4000], title=f"Corpo — {eid}", border_style="dim"))
        if not typer.confirm(f"Mover {eid} para a Lixeira?", default=False):
            console.print(f"[yellow]Pulado:[/yellow] {eid}\n")
            continue
        status = trash_message(eid)
        if status == "trashed":
            console.print(f"[green]Movido para a Lixeira:[/green] {eid}\n")
        elif status == "already":
            console.print(f"[yellow]Já havia sido movido (idempotente):[/yellow] {eid}\n")
        else:
            console.print(f"[red]Falha:[/red] {eid} — {status}\n")


@app.command()
def delete(
    email_agent_ids: list[str] = typer.Argument(..., help="Um ou mais IDs (E-YYYYMMDD-NNNNNN)."),
):
    """Move e-mail(s) para a Lixeira do provedor — mostra o corpo e confirma 1 a 1.

    Ação não-destrutiva (recuperável na Lixeira) e registrada em email_action_log."""
    _delete_impl(email_agent_ids)


@app.command()
def rm(
    email_agent_ids: list[str] = typer.Argument(..., help="Alias de `delete`."),
):
    """Alias de `delete`."""
    _delete_impl(email_agent_ids)


@app.command()
def digest(send: bool = typer.Option(False, "--send", help="Enviar via WhatsApp")):
    """Gera (e opcionalmente envia) o resumo matinal (1 a 4 mensagens)."""
    from email_agent.digest.builder import build_digest

    digest_obj = build_digest()
    for i, msg in enumerate(digest_obj.messages(), 1):
        console.print(f"[bold cyan]── mensagem {i} ──[/bold cyan]")
        console.print(msg)
        console.print()
    if send:
        from email_agent.connectors.evolution_client import send_text

        for m in digest_obj.messages():
            send_text(m)
        console.print(f"[green]{len(digest_obj.messages())} mensagem(ns) enviada(s) via WhatsApp.[/green]")


@app.command("run-morning")
def run_morning(
    send: bool = typer.Option(False, "--send", help="Também enviar o digest via WhatsApp"),
    bootstrap: bool = typer.Option(False, "--bootstrap", help="Janela completa na 1ª carga"),
):
    """Smoke test: roda o fluxo matinal inteiro de forma síncrona —
    sync de todas as contas → classifica pendentes → gera (e opcionalmente envia) o digest."""
    from email_agent.digest.builder import build_digest
    from email_agent.intelligence.graph import run_pipeline
    from email_agent.models import EmailClassification, EmailMessage
    from email_agent.workers.tasks_sync import sync_all_accounts

    console.print("[bold]1/3[/bold] Sincronizando contas…")
    sync_res = sync_all_accounts(bootstrap=bootstrap)
    console.print(sync_res)

    console.print("[bold]2/3[/bold] Classificando pendentes (síncrono)…")
    with db_session() as session:
        classified = select(EmailClassification.message_id)
        pending = (
            session.execute(select(EmailMessage.id).where(EmailMessage.id.not_in(classified)))
            .scalars()
            .all()
        )
    for db_id in pending:
        run_pipeline(db_id)
    console.print(f"  {len(pending)} mensagens classificadas")

    console.print("[bold]3/3[/bold] Gerando digest…")
    digest_obj = build_digest()
    for i, msg in enumerate(digest_obj.messages(), 1):
        console.print(f"[bold cyan]── mensagem {i} ──[/bold cyan]")
        console.print(msg)
        console.print()
    if send:
        from email_agent.connectors.evolution_client import send_text

        for m in digest_obj.messages():
            send_text(m)
        console.print(f"[green]{len(digest_obj.messages())} mensagem(ns) enviada(s).[/green]")


# ---------- sync / relabel ----------

@sync_app.command("all")
def sync_all(bootstrap: bool = typer.Option(False, "--bootstrap")):
    from email_agent.workers.tasks_sync import sync_all_accounts

    result = sync_all_accounts(bootstrap=bootstrap)
    console.print(result)


@sync_app.command("once")
def sync_once(account: str = typer.Option(..., "--account")):
    with db_session() as session:
        acc = session.execute(
            select(EmailAccount).where(EmailAccount.email_address == account)
        ).scalar_one_or_none()
        if acc is None:
            console.print(f"[red]Conta não cadastrada no banco:[/red] {account}")
            console.print(
                "Importe as contas declaradas em secrets/accounts.yml e tente novamente:\n"
                "  [bold]docker compose exec app email-agent accounts import-yaml[/bold]\n"
                f"  [bold]docker compose exec app email-agent sync once --account {account}[/bold]"
            )
            all_accs = session.execute(select(EmailAccount)).scalars().all()
            if all_accs:
                console.print("Contas atualmente cadastradas:")
                for a in all_accs:
                    console.print(f"  {a.email_address}")
            raise typer.Exit(1)
        account_id, provider = acc.id, acc.provider
    from email_agent.workers.tasks_sync import sync_one_account

    try:
        console.print(sync_one_account(account_id, provider, bootstrap=False))
    except Exception as exc:  # noqa: BLE001
        console.print(f"[red]Falha ao sincronizar {account}:[/red] {exc}")
        raise typer.Exit(1) from exc


@relabel_app.command("all")
def relabel_all():
    from email_agent.workers.tasks_classify import classify_pending

    console.print(classify_pending())


@relabel_app.command("message")
def relabel_message(id: str = typer.Option(..., "--id")):
    msg, _ = _load_message(id)
    from email_agent.intelligence.graph import run_pipeline

    state = run_pipeline(msg.id)
    console.print({k: state.get(k) for k in ("category", "priority", "suggested_labels")})


# ---------- rules ----------

@rules_app.command("import-yaml")
def rules_import(file: str = typer.Option(None, "--file", help="default: /secrets/rules.yml")):
    """Sincroniza email_rule com secrets/rules.yml."""
    from email_agent.connectors.rules_config import import_rules

    console.print(import_rules(file))


@rules_app.command("list")
def rules_list():
    from email_agent.models import EmailRule

    with db_session() as session:
        rules = session.execute(
            select(EmailRule).where(EmailRule.rule_type == "importance")
        ).scalars().all()
        t = Table()
        for col in ("Nome", "Escopo", "Ativa", "Resultado", "Descrição"):
            t.add_column(col)
        for r in rules:
            cond = r.condition_json or {}
            act = r.action_json or {}
            t.add_row(
                r.name, cond.get("scope", "*"), str(r.is_active),
                f"{act.get('priority','')} {','.join(act.get('labels',[]))}".strip(),
                (cond.get("description", "")[:60]),
            )
    console.print(t)


@rules_app.command("test")
def rules_test(email_agent_id: str):
    """Avalia as regras da conta contra uma mensagem específica (debug do agente LLM)."""
    from email_agent.intelligence.rule_agent import evaluate_rules_llm, load_rules_for_account

    msg, _ = _load_message(email_agent_id)
    with db_session() as session:
        account = session.get(EmailAccount, msg.account_id)
        rules = load_rules_for_account(session, account.email_address)
    if not rules:
        console.print(f"[yellow]Nenhuma regra para {account.email_address}.[/yellow]")
        raise typer.Exit()
    outcomes = evaluate_rules_llm(account.email_address, msg.subject or "", msg.from_email or "",
                                 msg.normalized_text or "", rules)
    console.print(outcomes or "[yellow]Nenhuma regra se aplicou.[/yellow]")


# ---------- review / train ----------

@review_app.command("export-labelstudio")
def review_export(
    label: str = typer.Option(None, "--label"),
    uncertain: bool = typer.Option(False, "--uncertain"),
    limit: int = typer.Option(500, "--limit"),
    output: str = typer.Option("/data/exports/labelstudio_tasks.json", "--output",
                               help="/data é o volume montado em ./data no host"),
):
    from email_agent.labelstudio.export import export_tasks

    n = export_tasks(output, ai_label=label, uncertain=uncertain, limit=limit)
    console.print(f"[green]{n} tasks exportadas para {output}[/green]")


@train_app.command("import-labelstudio")
def train_import(file: str):
    from email_agent.labelstudio.import_results import import_annotations

    console.print(f"[green]{import_annotations(file)} eventos de treino criados.[/green]")


@train_app.command("fit")
def train_fit():
    from email_agent.intelligence.training import derive_training_from_user_events, fit_spam_model

    derived = derive_training_from_user_events()
    trained = fit_spam_model()
    console.print(f"Eventos implícitos derivados: {derived} | amostras treinadas: {trained}")


# ---------- accounts / gmail ----------

@accounts_app.command("import-yaml")
def accounts_import_yaml(file: str = typer.Option(None, "--file", help="default: /secrets/accounts.yml")):
    """Sincroniza o banco com as contas declaradas em secrets/accounts.yml."""
    from email_agent.connectors.accounts_config import import_accounts

    console.print(import_accounts(file))


@accounts_app.command("add")
def accounts_add(
    email: str,
    provider: str = typer.Option(..., "--provider", help="gmail_api ou imap"),
    imap_host: str = typer.Option(None, "--imap-host"),
    imap_port: int = typer.Option(993, "--imap-port"),
):
    with db_session() as session:
        session.add(
            EmailAccount(
                provider=provider, email_address=email,
                imap_host=imap_host, imap_port=imap_port,
            )
        )
    console.print(f"[green]Conta {email} ({provider}) cadastrada.[/green]")


@accounts_app.command("list")
def accounts_list():
    with db_session() as session:
        accounts = session.execute(select(EmailAccount)).scalars().all()
        t = Table()
        for col in ("ID", "E-mail", "Provider", "Ativa", "Auth"):
            t.add_column(col)
        for a in accounts:
            color = "green" if a.auth_status == "ok" else "red"
            t.add_row(str(a.id), a.email_address, a.provider, str(a.is_active),
                      f"[{color}]{a.auth_status}[/{color}]")
    console.print(t)


@gmail_app.command("auth")
def gmail_auth(email: str):
    """Fluxo OAuth interativo. RODAR NO HOST (precisa de navegador), não no container:

        .venv/bin/email-agent gmail auth conta@gmail.com

    No host, exporte as variáveis apontando para as portas/paths publicados:
        DATABASE_URL=postgresql+psycopg://emailagent:emailagent@localhost:5433/emailagent
        GMAIL_OAUTH_CLIENT_SECRET_FILE=secrets/gmail_client_secret.json
        GMAIL_TOKEN_STORAGE_PATH=secrets/gmail_tokens
    """
    from email_agent.connectors.gmail_client import run_oauth_flow

    run_oauth_flow(email)  # salva o token em GMAIL_TOKEN_STORAGE_PATH/<email>.json
    console.print(f"[green]OAuth concluído para {email}.[/green] Token salvo.")

    # auth_status no banco é best-effort: se o DB não estiver acessível do host
    # (ex.: host 'postgres' não resolve fora do Docker), não perdemos o token — o
    # próximo sync atualiza o status.
    try:
        with db_session() as session:
            acc = session.execute(
                select(EmailAccount).where(EmailAccount.email_address == email)
            ).scalar_one_or_none()
            if acc:
                acc.auth_status = "ok"
            else:
                console.print(
                    f"[yellow]A conta {email} ainda não está cadastrada no banco.[/yellow]\n"
                    "Conclua com:\n"
                    "  [bold]docker compose exec app email-agent accounts import-yaml[/bold]\n"
                    f"  [bold]docker compose exec app email-agent sync once --account {email}[/bold]"
                )
    except Exception as exc:  # noqa: BLE001
        console.print(
            f"[yellow]Token salvo.[/yellow] Não marquei auth_status=ok no banco a partir do "
            f"host ({exc.__class__.__name__} — 'postgres' só resolve dentro do Docker).\n"
            "Importe a declaração da conta e rode o sync no container:\n"
            "  [bold]docker compose exec app email-agent accounts import-yaml[/bold]\n"
            f"  [bold]docker compose exec app email-agent sync once --account {email}[/bold]"
        )


if __name__ == "__main__":
    app()
