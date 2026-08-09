"""Componentes de interação estilo DOS: menu por setas e campos de texto.

Fundo azul + bordas duplas (box.DOUBLE) para o ar vintage. Navegação com ↑/↓ e
Enter; Esc cancela. Renderização redesenha o painel a cada tecla (sem dependência
de framework de tela cheia).
"""
from __future__ import annotations

import readchar
from rich import box
from rich.align import Align
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Prompt
from rich.table import Table
from rich.text import Text

DOS = "bright_white on blue"
DOS_SEL = "black on bright_cyan"
DOS_TITLE = "bold bright_yellow on blue"


def banner(console: Console, subtitle: str = "") -> None:
    title = Text("EMAIL-AGENT · CONSOLE DE CONFIGURAÇÃO", style=DOS_TITLE, justify="center")
    if subtitle:
        title.append("\n" + subtitle, style=DOS)
    console.print(Panel(Align.center(title), box=box.DOUBLE, style=DOS, padding=(0, 2)))


def select_menu(
    console: Console,
    title: str,
    options: list[str],
    footer: str = "↑/↓ mover · Enter selecionar · Esc voltar",
) -> int | None:
    """Menu navegável. Retorna o índice escolhido ou None se cancelado (Esc/q)."""
    idx = 0
    while True:
        console.clear()
        banner(console)
        table = Table(show_header=False, box=None, expand=True, padding=(0, 1))
        for i, opt in enumerate(options):
            marker = "►" if i == idx else " "
            style = DOS_SEL if i == idx else DOS
            table.add_row(Text(f" {marker} {opt} ", style=style))
        console.print(Panel(table, title=title, box=box.DOUBLE, style=DOS, padding=(1, 2)))
        console.print(Text(" " + footer, style=DOS))

        key = readchar.readkey()
        if key == readchar.key.UP:
            idx = (idx - 1) % len(options)
        elif key == readchar.key.DOWN:
            idx = (idx + 1) % len(options)
        elif key in (readchar.key.ENTER, "\r", "\n"):
            return idx
        elif key in (readchar.key.ESC, "q", "Q"):
            return None


def ask(label: str, default: str = "", password: bool = False) -> str:
    """Campo de texto com prompt Rich (estilo DOS)."""
    prompt = Text(f" {label} ", style=DOS)
    return Prompt.ask(prompt, default=default or None, password=password, show_default=not password) or ""


def confirm(console: Console, message: str) -> bool:
    res = select_menu(console, message, ["Sim", "Não"], footer="↑/↓ · Enter")
    return res == 0


def info_panel(console: Console, title: str, body: str, ok: bool = True) -> None:
    show_panel(console, title, body, ok=ok)


def show_panel(
    console: Console,
    title: str,
    body: str,
    footer: str = "Pressione qualquer tecla para continuar…",
    ok: bool = True,
) -> str:
    """Renderiza um painel e devolve a tecla pressionada (para atalhos como R=refresh)."""
    style = "bright_white on blue" if ok else "bright_white on red"
    console.clear()
    banner(console)
    console.print(Panel(Text(body or "(sem saída)", style=style), title=title, box=box.DOUBLE, style=style))
    console.print(Text(" " + footer, style=DOS))
    return readchar.readkey()
