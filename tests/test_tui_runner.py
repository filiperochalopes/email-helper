import subprocess
from types import SimpleNamespace

from email_agent.tui import runner


def _fake_run(stdout="", returncode=0):
    def _run(cmd, capture_output, text, timeout, check=False):
        _run.cmd = cmd
        return SimpleNamespace(stdout=stdout, stderr="", returncode=returncode)
    return _run


def test_stack_is_up_true(monkeypatch):
    monkeypatch.setattr(runner.shutil, "which", lambda _: "/usr/bin/docker")
    monkeypatch.setattr(runner.subprocess, "run",
                        lambda *a, **k: SimpleNamespace(stdout="email-helper-db\nemail-helper-app\n"))
    assert runner.stack_is_up("email-helper-app") is True


def test_stack_is_up_false_without_docker(monkeypatch):
    monkeypatch.setattr(runner.shutil, "which", lambda _: None)
    assert runner.stack_is_up("email-helper-app") is False


def test_run_in_stack_uses_docker_when_up(monkeypatch):
    monkeypatch.setattr(runner, "stack_is_up", lambda service="email-helper-app": True)
    fake = _fake_run(stdout="ok")
    monkeypatch.setattr(runner.subprocess, "run", fake)
    res = runner.run_in_stack(["accounts", "import-yaml"])
    assert res.ok and res.where == "docker"
    assert fake.cmd[:5] == ["docker", "compose", "exec", "-T", "email-helper-app"]
    assert fake.cmd[5] == "agent"


def test_run_in_stack_falls_back_to_host(monkeypatch):
    monkeypatch.setattr(runner, "stack_is_up", lambda service="email-helper-app": False)
    monkeypatch.setattr(runner.shutil, "which", lambda _: "/venv/bin/agent")
    fake = _fake_run(stdout="ok")
    monkeypatch.setattr(runner.subprocess, "run", fake)
    res = runner.run_in_stack(["accounts", "list"])
    assert res.where == "host"
    assert fake.cmd[0] == "/venv/bin/agent"


def test_run_on_host_never_uses_docker(monkeypatch):
    monkeypatch.setattr(runner.shutil, "which", lambda _: "/venv/bin/agent")
    fake = _fake_run()
    monkeypatch.setattr(runner.subprocess, "run", fake)
    runner.run_on_host(["gmail", "auth", "a@gmail.com"])
    assert "docker" not in fake.cmd


def test_run_handles_timeout(monkeypatch):
    def _boom(*a, **k):
        raise subprocess.TimeoutExpired(cmd="x", timeout=1)
    monkeypatch.setattr(runner, "stack_is_up", lambda service="email-helper-app": False)
    monkeypatch.setattr(runner.shutil, "which", lambda _: "agent")
    monkeypatch.setattr(runner.subprocess, "run", _boom)
    res = runner.run_in_stack(["accounts", "list"])
    assert res.ok is False and res.returncode == 124
