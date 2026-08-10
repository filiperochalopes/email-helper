from contextlib import contextmanager
from types import SimpleNamespace

from typer.testing import CliRunner

from email_agent.cli import app as cli
from email_agent.connectors import gmail_client

runner = CliRunner()


class _Result:
    def __init__(self, one=None, many=None):
        self._one = one
        self._many = many or []

    def scalar_one_or_none(self):
        return self._one

    def scalars(self):
        return self

    def all(self):
        return self._many


class _Session:
    def __init__(self, results):
        self._results = iter(results)

    def execute(self, _statement):
        return next(self._results)


def _db_session_with(*results):
    @contextmanager
    def _db_session():
        yield _Session(results)

    return _db_session


def test_sync_once_missing_account_explains_import(monkeypatch):
    monkeypatch.setattr(cli, "db_session", _db_session_with(_Result(), _Result()))

    result = runner.invoke(cli.app, ["sync", "once", "--account", "filipe@noharm.ai"])

    assert result.exit_code == 1
    assert "Conta não cadastrada no banco" in result.output
    assert "accounts import-yaml" in result.output
    assert "sync once --account filipe@noharm.ai" in result.output
    assert "Traceback" not in result.output


def test_sync_once_reports_provider_failure(monkeypatch):
    account = SimpleNamespace(id=10, provider="gmail_api")
    monkeypatch.setattr(cli, "db_session", _db_session_with(_Result(one=account)))

    from email_agent.sync import service

    monkeypatch.setattr(
        service,
        "sync_one_account",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("OAuth inválido")),
    )

    result = runner.invoke(cli.app, ["sync", "once", "--account", "filipe@noharm.ai"])

    assert result.exit_code == 1
    assert "Falha ao sincronizar filipe@noharm.ai" in result.output
    assert "OAuth inválido" in result.output
    assert "new_messages" not in result.output
    assert "Traceback" not in result.output


def test_gmail_auth_missing_account_explains_import(monkeypatch):
    monkeypatch.setattr(gmail_client, "run_oauth_flow", lambda _email: None)
    monkeypatch.setattr(cli, "db_session", _db_session_with(_Result()))

    result = runner.invoke(cli.app, ["gmail", "auth", "filipe@noharm.ai"])

    assert result.exit_code == 0
    assert "ainda não está cadastrada no banco" in result.output
    assert "accounts import-yaml" in result.output
    assert "sync once --account filipe@noharm.ai" in result.output


def test_gmail_auth_database_unavailable_explains_import(monkeypatch):
    monkeypatch.setattr(gmail_client, "run_oauth_flow", lambda _email: None)

    @contextmanager
    def unavailable_db_session():
        raise RuntimeError("database unavailable")
        yield SimpleNamespace()

    monkeypatch.setattr(cli, "db_session", unavailable_db_session)

    result = runner.invoke(cli.app, ["gmail", "auth", "filipe@noharm.ai"])

    assert result.exit_code == 0
    assert "RuntimeError" in result.output
    assert "accounts import-yaml" in result.output
    assert "sync once --account filipe@noharm.ai" in result.output


def test_relabel_legacy_uses_bounded_batch(monkeypatch):
    from email_agent.sync import service

    received = []
    monkeypatch.setattr(
        service,
        "reclassify_legacy_inbox",
        lambda limit: received.append(limit)
        or {"reclassified": limit, "cleanup_candidates": 3, "errors": 0},
    )

    result = runner.invoke(cli.app, ["relabel", "legacy", "--limit", "12"])

    assert result.exit_code == 0
    assert received == [12]
    assert "cleanup_candidates" in result.output
