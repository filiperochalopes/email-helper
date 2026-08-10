"""Dispara comandos do email-agent a partir do TUI (host).

Duas rotas:
- `run_in_stack`: comandos que tocam o banco (import-yaml, accounts list). Preferem
  `docker compose exec -T app email-agent ...` quando o stack está de pé (o DB resolve
  como 'postgres' dentro da rede), caindo para execução local se o Docker não estiver up.
- `run_on_host`: comandos que precisam do host (gmail auth abre o navegador).
"""
from __future__ import annotations

import shutil
import subprocess
import sys
from dataclasses import dataclass


@dataclass
class RunResult:
    ok: bool
    where: str  # "docker" | "host"
    stdout: str
    stderr: str
    returncode: int


def _local_argv() -> list[str]:
    """email-agent local: usa o console-script se existir, senão o módulo do venv atual."""
    exe = shutil.which("email-agent")
    if exe:
        return [exe]
    return [sys.executable, "-m", "email_agent.cli.app"]


def stack_is_up(service: str = "app") -> bool:
    """True se o serviço Docker (default 'app') estiver rodando."""
    if not shutil.which("docker"):
        return False
    try:
        out = subprocess.run(
            ["docker", "compose", "ps", "--status", "running", "--services"],
            capture_output=True, text=True, timeout=10, check=False,
        )
    except (subprocess.SubprocessError, OSError):
        return False
    return service in out.stdout.split()


def run_in_stack(args: list[str], service: str = "app", timeout: int = 300) -> RunResult:
    """Roda `email-agent <args>` no container (se up) ou local."""
    if stack_is_up(service):
        cmd = ["docker", "compose", "exec", "-T", service, "email-agent", *args]
        where = "docker"
    else:
        cmd = [*_local_argv(), *args]
        where = "host"
    return _run(cmd, where, timeout)


def run_on_host(args: list[str], timeout: int = 300) -> RunResult:
    """Roda `email-agent <args>` sempre no host (ex.: gmail auth, abre navegador)."""
    return _run([*_local_argv(), *args], "host", timeout)


def _run(cmd: list[str], where: str, timeout: int) -> RunResult:
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=False)
    except subprocess.TimeoutExpired as exc:
        return RunResult(False, where, exc.stdout or "", f"timeout após {timeout}s", 124)
    except OSError as exc:
        return RunResult(False, where, "", str(exc), 127)
    return RunResult(proc.returncode == 0, where, proc.stdout, proc.stderr, proc.returncode)
