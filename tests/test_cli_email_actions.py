import json
from contextlib import contextmanager
from types import SimpleNamespace

from typer.testing import CliRunner

from email_agent.cli import app as cli

runner = CliRunner()


def _fake_msg(**over):
    base = dict(
        email_agent_id="E-20260612-000001", account_id=1, from_name="Loja",
        from_email="promo@spam.example.com", subject="Oferta", date="2026-06-12",
        mailbox="INBOX", ai_labels=["AI/Marketing"], normalized_text="corpo do e-mail",
        snippet="resumo",
    )
    base.update(over)
    return SimpleNamespace(**base)


def _patch_load(monkeypatch, msg, cls=None):
    monkeypatch.setattr(cli, "_load_message", lambda _eid: (msg, cls))


def test_show_json_is_parseable(monkeypatch):
    msg = _fake_msg()
    cls = SimpleNamespace(priority="P2", category="marketing", digest_summary="oferta",
                          importance_reason="newsletter")
    _patch_load(monkeypatch, msg, cls)

    @contextmanager
    def _db():
        yield SimpleNamespace(get=lambda _model, _id: SimpleNamespace(email_address="me@x.com"))
    monkeypatch.setattr(cli, "db_session", _db)

    result = runner.invoke(cli.app, ["show", "E-20260612-000001", "--json"])
    assert result.exit_code == 0
    payload = json.loads(result.stdout.strip().splitlines()[-1])
    assert payload["from_email"] == "promo@spam.example.com"
    assert payload["priority"] == "P2"
    assert payload["ai_labels"] == ["AI/Marketing"]


def test_delete_yes_skips_confirmation(monkeypatch):
    _patch_load(monkeypatch, _fake_msg())
    from email_agent.actions import delete_actions
    monkeypatch.setattr(delete_actions, "trash_message", lambda _eid: "trashed")

    result = runner.invoke(cli.app, ["delete", "E-20260612-000001", "--yes"])
    assert result.exit_code == 0
    assert "Movido para a Lixeira" in result.output
    # não deve ter pedido confirmação
    assert "Mover" not in result.output or "Lixeira?" not in result.output


def test_label_set_rejects_invalid(monkeypatch):
    _patch_load(monkeypatch, _fake_msg())
    result = runner.invoke(cli.app, ["label", "E-20260612-000001", "--set", "AI/Inexistente"])
    assert result.exit_code == 1
    assert "Label inválida" in result.output
