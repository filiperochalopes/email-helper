"""TUI vintage para contas e regras. Entrypoint: `email-agent tui`.

Fluxo: edita o YAML (fonte de verdade) → `import-yaml` sincroniza o banco. Reauth
do Gmail roda no host (abre navegador). Nada aqui deleta e-mails nem chama LLM
externo — apenas orquestra os comandos já existentes da CLI.
"""
from __future__ import annotations

import json

from rich.console import Console
from rich.table import Table

from email_agent.tui import runner, yaml_store as ys
from email_agent.tui.widgets import ask, banner, confirm, info_panel, select_menu, show_panel

console = Console()


def main() -> None:
    while True:
        choice = select_menu(
            console,
            "MENU PRINCIPAL",
            ["Contas de e-mail", "Regras de importância", "Abrir e-mail por ID",
             "Arquivar antigos (AI/Archive)", "Status de autenticação (banco)", "Sair"],
            footer="↑/↓ mover · Enter selecionar · Esc/q sair",
        )
        if choice in (None, 5):
            console.clear()
            console.print("Até logo.")
            return
        if choice == 0:
            accounts_screen()
        elif choice == 1:
            rules_screen()
        elif choice == 2:
            email_screen()
        elif choice == 3:
            archive_screen()
        elif choice == 4:
            _show_auth_status()


# ---------- contas ----------

def accounts_screen() -> None:
    while True:
        data = ys.load_accounts()
        gmail = list(data.get("gmail") or [])
        imap = list(data.get("imap") or [])
        listing = [f"[Gmail] {e.get('email')}  ({e.get('display_name', '—')})" for e in gmail]
        listing += [f"[IMAP]  {e.get('email')}  @ {e.get('host', '?')}" for e in imap]

        options = [
            "Adicionar conta Gmail",
            "Adicionar conta IMAP",
            "Editar conta existente",
            "Remover conta",
            "Reautenticar Gmail (OAuth, abre navegador)",
            "Salvar e importar para o banco (import-yaml)",
            "Voltar",
        ]
        title = "CONTAS  ·  " + (f"{len(gmail)} Gmail / {len(imap)} IMAP" if (gmail or imap) else "nenhuma declarada")
        if listing:
            info = "\n".join(listing)
            console.clear(); banner(console)
            console.print(info)
            console.input("\n[bright_white on blue] Enter para abrir o menu [/]")
        choice = select_menu(console, title, options)

        if choice in (None, 6):
            return
        if choice == 0:
            _add_gmail(data)
        elif choice == 1:
            _add_imap(data)
        elif choice == 2:
            _edit_account(data, gmail, imap)
        elif choice == 3:
            _remove_account(data, gmail, imap)
        elif choice == 4:
            _reauth_gmail(gmail)
        elif choice == 5:
            _save_and_import_accounts(data)


def _add_gmail(data) -> None:
    email = ask("E-mail Gmail")
    if not email:
        return
    name = ask("Nome de exibição (opcional)")
    try:
        ys.upsert_gmail(data, email, name)
        ys.save_accounts(data)
        info_panel(console, "OK", f"Conta Gmail {email} salva em accounts.yml.\n"
                   "Lembre de reautenticar (OAuth) e importar para o banco.")
    except ys.ValidationError as exc:
        info_panel(console, "ERRO", str(exc), ok=False)


def _add_imap(data) -> None:
    entry = {
        "email": ask("E-mail"),
        "display_name": ask("Nome de exibição (opcional)"),
        "host": ask("Host IMAP"),
        "port": ask("Porta", default="993"),
        "username": ask("Usuário"),
        "password": ask("Senha", password=True),
    }
    if not entry["email"]:
        return
    try:
        ys.upsert_imap(data, entry)
        ys.save_accounts(data)
        info_panel(console, "OK", f"Conta IMAP {entry['email']} salva em accounts.yml.")
    except ys.ValidationError as exc:
        info_panel(console, "ERRO", str(exc), ok=False)


def _edit_account(data, gmail, imap) -> None:
    emails = [e.get("email") for e in gmail] + [e.get("email") for e in imap]
    if not emails:
        info_panel(console, "AVISO", "Nenhuma conta para editar.")
        return
    sel = select_menu(console, "EDITAR — escolha a conta", emails + ["Cancelar"])
    if sel is None or sel == len(emails):
        return
    is_gmail = sel < len(gmail)
    current = (gmail + imap)[sel]
    if is_gmail:
        name = ask("Nome de exibição", default=current.get("display_name", ""))
        ys.upsert_gmail(data, current["email"], name)
    else:
        entry = {
            "email": current["email"],
            "display_name": ask("Nome de exibição", default=current.get("display_name", "")),
            "host": ask("Host IMAP", default=current.get("host", "")),
            "port": ask("Porta", default=str(current.get("port", 993))),
            "username": ask("Usuário", default=current.get("username", "")),
            "password": ask("Senha (Enter mantém)", default=current.get("password", ""), password=True),
        }
        try:
            ys.upsert_imap(data, entry)
        except ys.ValidationError as exc:
            info_panel(console, "ERRO", str(exc), ok=False)
            return
    ys.save_accounts(data)
    info_panel(console, "OK", f"Conta {current['email']} atualizada.")


def _remove_account(data, gmail, imap) -> None:
    emails = [e.get("email") for e in gmail] + [e.get("email") for e in imap]
    if not emails:
        info_panel(console, "AVISO", "Nenhuma conta para remover.")
        return
    sel = select_menu(console, "REMOVER — escolha a conta", emails + ["Cancelar"])
    if sel is None or sel == len(emails):
        return
    email = emails[sel]
    if confirm(console, f"Remover {email} do accounts.yml?"):
        ys.remove_account(data, email)
        ys.save_accounts(data)
        info_panel(console, "OK", f"{email} removida do YAML.\n"
                   "No próximo import-yaml a conta será desativada no banco (não apagada).")


def _reauth_gmail(gmail) -> None:
    emails = [e.get("email") for e in gmail]
    if not emails:
        info_panel(console, "AVISO", "Nenhuma conta Gmail declarada.")
        return
    sel = select_menu(console, "REAUTENTICAR — escolha a conta", emails + ["Cancelar"])
    if sel is None or sel == len(emails):
        return
    email = emails[sel]
    console.clear(); banner(console)
    console.print(f"Abrindo fluxo OAuth para [bold]{email}[/bold] no navegador…\n")
    auth = runner.run_on_host(["gmail", "auth", email])
    if not auth.ok:
        info_panel(console, "FALHA OAuth", (auth.stdout + "\n" + auth.stderr).strip(), ok=False)
        return

    # OAuth ok no host (token salvo), mas o auth_status no banco só é gravável de dentro
    # do Docker (onde 'postgres' resolve). Encadeia import-yaml + sync once para que o
    # próprio sync marque auth_status=ok, sem o usuário rodar comando à mão.
    log = [auth.stdout.strip(), f"[OAuth concluído para {email}]"]
    console.print("Token salvo. Sincronizando para marcar auth_status no banco…\n")
    imp = runner.run_in_stack(["accounts", "import-yaml"])
    log.append(f"\n— import-yaml ({imp.where}) —\n{(imp.stdout + imp.stderr).strip()}")
    syn = runner.run_in_stack(["sync", "once", "--account", email])
    log.append(f"\n— sync once ({syn.where}) —\n{(syn.stdout + syn.stderr).strip()}")
    ok = imp.ok and syn.ok
    title = f"Reautenticação concluída — {email}" if ok else "Reautenticação com avisos"
    info_panel(console, title, "\n".join(p for p in log if p), ok=ok)


def _save_and_import_accounts(data) -> None:
    ys.save_accounts(data)
    console.clear(); banner(console)
    console.print("Importando contas para o banco…\n")
    res = runner.run_in_stack(["accounts", "import-yaml"])
    info_panel(console, f"import-yaml ({res.where})" if res.ok else "FALHA import-yaml",
               (res.stdout + "\n" + res.stderr).strip(), ok=res.ok)


def _show_auth_status() -> None:
    while True:
        console.clear(); banner(console)
        console.print("Consultando status no banco…\n")
        res = runner.run_in_stack(["accounts", "list"])
        key = show_panel(
            console,
            f"accounts list ({res.where})" if res.ok else "FALHA",
            (res.stdout + "\n" + res.stderr).strip(),
            footer="R sincronizar todas e revalidar · qualquer outra tecla volta",
            ok=res.ok,
        )
        if key not in ("r", "R"):
            return
        console.clear(); banner(console)
        console.print("Sincronizando todas as contas (valida autenticação)…")
        console.print("[dim]Pode levar alguns minutos com muitas contas IMAP. Aguarde…[/dim]\n")
        syn = runner.run_in_stack(["sync", "all"], timeout=900)
        show_panel(console, f"sync all ({syn.where})" if syn.ok else "FALHA sync",
                   (syn.stdout + "\n" + syn.stderr).strip(),
                   footer="Pressione qualquer tecla para ver a tabela atualizada…", ok=syn.ok)
        # volta ao topo do loop → reexecuta accounts list com o status revalidado


# ---------- e-mail ----------

def _fetch_email(eid: str) -> dict | None:
    res = runner.run_in_stack(["show", eid, "--json"])
    if not res.ok:
        info_panel(console, "FALHA", (res.stdout + "\n" + res.stderr).strip(), ok=False)
        return None
    for line in res.stdout.splitlines():
        line = line.strip()
        if line.startswith("{"):
            try:
                return json.loads(line)
            except json.JSONDecodeError:
                continue
    info_panel(console, "AVISO", f"Mensagem {eid} não encontrada.\n\n{res.stdout}", ok=False)
    return None


def _render_email(data: dict) -> None:
    console.clear(); banner(console)
    t = Table(show_header=False, box=None, expand=True, style="bright_white on blue")
    t.add_column(style="bold bright_yellow on blue", no_wrap=True)
    t.add_column(style="bright_white on blue")
    t.add_row("ID", data.get("id", ""))
    t.add_row("Conta", data.get("account", ""))
    t.add_row("De", f"{data.get('from_name', '')} <{data.get('from_email', '')}>")
    t.add_row("Assunto", data.get("subject", "(sem assunto)"))
    t.add_row("Data", data.get("date", ""))
    t.add_row("Pasta", data.get("mailbox", ""))
    t.add_row("Labels AI", ", ".join(data.get("ai_labels") or []) or "—")
    t.add_row("Prioridade", data.get("priority") or "—")
    t.add_row("Categoria", data.get("category") or "—")
    t.add_row("Resumo", data.get("summary") or "—")
    console.print(t)
    body = (data.get("body") or "").strip()
    if body:
        console.print()
        console.print(body[:1500])
    console.print()


def email_screen() -> None:
    eid = ask("ID do e-mail (E-YYYYMMDD-NNNNNN)")
    if not eid:
        return
    eid = eid.strip()
    while True:
        data = _fetch_email(eid)
        if data is None:
            return
        _render_email(data)
        console.input("[bright_white on blue] Enter para as ações [/]")
        choice = select_menu(
            console,
            f"AÇÕES · {eid}",
            ["Excluir (mover para a Lixeira)", "Arquivar (AI/Archive)",
             "Categorizar (aplicar label AI)",
             "Criar regra com o domínio do remetente", "Recarregar", "Voltar"],
        )
        if choice in (None, 5):
            return
        if choice == 0:
            _email_delete(eid)
            return
        if choice == 1:
            _email_archive(eid)
            return
        if choice == 2:
            _email_categorize(eid)
        elif choice == 3:
            _email_rule_from_domain(data)
        # choice == 4 (Recarregar) → loop


def _email_delete(eid: str) -> None:
    if not confirm(console, f"Mover {eid} para a Lixeira? (recuperável)"):
        return
    res = runner.run_in_stack(["delete", eid, "--yes"])
    info_panel(console, "Excluído" if res.ok else "FALHA",
               (res.stdout + "\n" + res.stderr).strip(), ok=res.ok)


def _email_archive(eid: str) -> None:
    if not confirm(console, f"Mover {eid} para AI/Archive? (sai da INBOX, recuperável)"):
        return
    res = runner.run_in_stack(["archive-one", eid])
    info_panel(console, "Arquivado" if res.ok else "FALHA",
               (res.stdout + "\n" + res.stderr).strip(), ok=res.ok)


def archive_screen() -> None:
    """Fluxo manual de arquivamento por cutoff de data (todas as contas)."""
    before = ask("Arquivar e-mails da INBOX anteriores a (YYYY-MM-DD)")
    if not before:
        return
    before = before.strip()
    if not confirm(console, f"Arquivar tudo na INBOX antes de {before} em AI/Archive?"):
        return
    res = runner.run_in_stack(["archive", "--before", before, "--yes"], timeout=900)
    info_panel(console, "Arquivamento" if res.ok else "FALHA",
               (res.stdout + "\n" + res.stderr).strip(), ok=res.ok)


def _email_categorize(eid: str) -> None:
    from email_agent.intelligence.taxonomy import ALL_AI_LABELS

    sel = select_menu(console, "Aplicar qual label AI?", [*ALL_AI_LABELS, "Cancelar"])
    if sel is None or sel == len(ALL_AI_LABELS):
        return
    label = ALL_AI_LABELS[sel]
    res = runner.run_in_stack(["label", eid, "--set", label])
    info_panel(console, "Label aplicada" if res.ok else "FALHA",
               (res.stdout + "\n" + res.stderr).strip(), ok=res.ok)


def _email_rule_from_domain(data: dict) -> None:
    from_email = (data.get("from_email") or "").strip()
    domain = from_email.split("@")[-1] if "@" in from_email else ""
    if not domain:
        info_panel(console, "AVISO", "Remetente sem domínio identificável.", ok=False)
        return
    prefilled = ys.spam_domain_rule(domain)
    console.clear(); banner(console)
    console.print(f"Pré-preenchido para o domínio [bold]{domain}[/bold]. "
                  "Ajuste como quiser (Enter mantém o sugerido).\n")
    entry = _rule_form(prefilled)
    if not entry:
        return
    rules_data = ys.load_rules()
    try:
        ys.upsert_rule(rules_data, entry)
        ys.save_rules(rules_data)
    except ys.ValidationError as exc:
        info_panel(console, "ERRO", str(exc), ok=False)
        return
    console.print("\nImportando regra para o banco…")
    res = runner.run_in_stack(["rules", "import-yaml"])
    info_panel(console, "Regra criada" if res.ok else "Regra salva (import com avisos)",
               f"Regra '{entry['name']}' para {domain}.\n\n"
               + (res.stdout + "\n" + res.stderr).strip(), ok=res.ok)


# ---------- regras ----------

def rules_screen() -> None:
    while True:
        data = ys.load_rules()
        rules = list(data.get("rules") or [])
        listing = [f"{r.get('name')}  [{r.get('scope', '*')}]  → {dict(r.get('outcome') or {})}" for r in rules]
        options = [
            "Adicionar regra",
            "Marcar domínio como spam suspeito",
            "Editar regra",
            "Remover regra",
            "Salvar e importar para o banco (import-yaml)",
            "Voltar",
        ]
        if listing:
            console.clear(); banner(console)
            console.print("\n".join(listing))
            console.input("\n[bright_white on blue] Enter para abrir o menu [/]")
        choice = select_menu(console, f"REGRAS · {len(rules)} declarada(s)", options)

        if choice in (None, 5):
            return
        if choice == 0:
            _add_rule(data)
        elif choice == 1:
            _spam_domain(data)
        elif choice == 2:
            _edit_rule(data, rules)
        elif choice == 3:
            _remove_rule(data, rules)
        elif choice == 4:
            _save_and_import_rules(data)


def _rule_form(current: dict | None = None) -> dict | None:
    current = current or {}
    outcome = current.get("outcome") or {}
    name = ask("Nome único", default=current.get("name", ""))
    if not name:
        return None
    return {
        "name": name,
        "scope": ask("Escopo (e-mail da conta ou *)", default=current.get("scope", "*")),
        "description": ask("Descrição em pt-BR para o LLM", default=current.get("description", "")),
        "outcome": {
            "priority": ask("Prioridade (P0/P1/P2/ignore, opcional)", default=outcome.get("priority", "")),
            "category": ask("Categoria (opcional)", default=outcome.get("category", "")),
            "labels": ask("Labels AI separadas por vírgula (opcional)",
                          default=",".join(outcome.get("labels", []))),
        },
    }


def _add_rule(data) -> None:
    entry = _rule_form()
    if not entry:
        return
    try:
        ys.upsert_rule(data, entry)
        ys.save_rules(data)
        info_panel(console, "OK", f"Regra '{entry['name']}' salva em rules.yml.")
    except ys.ValidationError as exc:
        info_panel(console, "ERRO", str(exc), ok=False)


def _spam_domain(data) -> None:
    domain = ask("Domínio a marcar como spam suspeito (ex.: promo.exemplo.com)")
    if not domain:
        return
    entry = ys.spam_domain_rule(domain)
    try:
        ys.upsert_rule(data, entry)
        ys.save_rules(data)
        info_panel(console, "OK",
                   f"Regra '{entry['name']}' criada: mensagens de {domain} → AI/Spam Suspeito.\n"
                   "(Política do MVP: nunca deleta automaticamente, só aplica a label.)")
    except ys.ValidationError as exc:
        info_panel(console, "ERRO", str(exc), ok=False)


def _edit_rule(data, rules) -> None:
    if not rules:
        info_panel(console, "AVISO", "Nenhuma regra para editar.")
        return
    names = [r.get("name") for r in rules]
    sel = select_menu(console, "EDITAR — escolha a regra", names + ["Cancelar"])
    if sel is None or sel == len(names):
        return
    current = dict(rules[sel])
    current["outcome"] = dict(current.get("outcome") or {})
    entry = _rule_form(current)
    if not entry:
        return
    try:
        ys.upsert_rule(data, entry)
        ys.save_rules(data)
        info_panel(console, "OK", f"Regra '{entry['name']}' atualizada.")
    except ys.ValidationError as exc:
        info_panel(console, "ERRO", str(exc), ok=False)


def _remove_rule(data, rules) -> None:
    if not rules:
        info_panel(console, "AVISO", "Nenhuma regra para remover.")
        return
    names = [r.get("name") for r in rules]
    sel = select_menu(console, "REMOVER — escolha a regra", names + ["Cancelar"])
    if sel is None or sel == len(names):
        return
    if confirm(console, f"Remover regra '{names[sel]}'?"):
        ys.remove_rule(data, names[sel])
        ys.save_rules(data)
        info_panel(console, "OK", f"Regra '{names[sel]}' removida do YAML.")


def _save_and_import_rules(data) -> None:
    ys.save_rules(data)
    console.clear(); banner(console)
    console.print("Importando regras para o banco…\n")
    res = runner.run_in_stack(["rules", "import-yaml"])
    info_panel(console, f"import-yaml ({res.where})" if res.ok else "FALHA import-yaml",
               (res.stdout + "\n" + res.stderr).strip(), ok=res.ok)


if __name__ == "__main__":
    main()
