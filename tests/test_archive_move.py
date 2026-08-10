"""Política de labels e arquivo nativo."""
from types import SimpleNamespace

import pytest
from typer.testing import CliRunner

from email_agent.actions import archive_actions as aa
from email_agent.actions import gmail_actions, imap_actions, safety_gate
from email_agent.cli import app as cli
from email_agent.connectors.imap_client import discover_folders, ensure_archive_folder
from email_agent.intelligence.taxonomy import (
    LABEL_FOCO,
    LABEL_SPAM_SUSPEITO,
    imap_destination,
    imap_keyword,
    moves_out_of_inbox,
)

runner = CliRunner()


# ---------- taxonomia: o que sai da INBOX e qual pasta IMAP vence ----------

@pytest.mark.parametrize("label,expected", [
    (LABEL_FOCO, False),
    (LABEL_SPAM_SUSPEITO, True),
    ("AI/Inexistente", False),
])
def test_moves_out_of_inbox(label, expected):
    assert moves_out_of_inbox(label) is expected


def test_imap_destination_priority():
    # nenhuma label move -> fica na INBOX (None)
    assert imap_destination([LABEL_FOCO]) is None
    assert imap_destination([LABEL_FOCO, LABEL_SPAM_SUSPEITO]) == LABEL_SPAM_SUSPEITO


# ---------- apply_label: move vs stay por provedor ----------

def _account(provider):
    return SimpleNamespace(provider=provider, email_address="me@x.com")


def _msg():
    return SimpleNamespace(provider_message_id="INBOX:1:42", mailbox="INBOX",
                           ai_labels=[], email_agent_id="E-1")


def test_apply_label_gmail_move_removes_inbox(monkeypatch):
    calls = []
    monkeypatch.setattr(gmail_actions, "add_label", lambda *a: calls.append(("add", a[2])))
    monkeypatch.setattr(gmail_actions, "move_to_label", lambda *a: calls.append(("move", a[2])))

    safety_gate.apply_label(_account("gmail_api"), _msg(), LABEL_SPAM_SUSPEITO)
    assert calls == [("move", LABEL_SPAM_SUSPEITO)]


def test_apply_label_gmail_keep_only_adds(monkeypatch):
    calls = []
    monkeypatch.setattr(gmail_actions, "add_label", lambda *a: calls.append(("add", a[2])))
    monkeypatch.setattr(gmail_actions, "move_to_label", lambda *a: calls.append(("move", a[2])))

    safety_gate.apply_label(_account("gmail_api"), _msg(), LABEL_FOCO)
    assert calls == [("add", LABEL_FOCO)]


def test_apply_label_imap_move_updates_mailbox(monkeypatch):
    moved = []
    monkeypatch.setattr(imap_actions, "move_to_ai_folder",
                        lambda acc, m, lb: moved.append(lb) or "AI.Spam Suspeito")
    msg = _msg()
    safety_gate.apply_label(_account("imap"), msg, LABEL_SPAM_SUSPEITO)
    assert moved == [LABEL_SPAM_SUSPEITO]
    assert msg.mailbox == "AI.Spam Suspeito"


def test_apply_label_imap_keep_uses_keyword(monkeypatch):
    moved, kw = [], []
    monkeypatch.setattr(imap_actions, "move_to_ai_folder",
                        lambda acc, m, lb: moved.append(lb) or "AI.Foco")
    monkeypatch.setattr(imap_actions, "add_keyword",
                        lambda acc, m, lb: kw.append(lb) or True)
    msg = _msg()
    safety_gate.apply_label(_account("imap"), msg, LABEL_FOCO)
    assert moved == []                 # não cria pasta no IMAP para label que fica na INBOX
    assert kw == [LABEL_FOCO]         # vira keyword (etiqueta no lugar)
    assert msg.mailbox == "INBOX"


def test_imap_keyword_naming():
    assert imap_keyword(LABEL_FOCO) == "AI_Foco"
    # sem '/' nem espaço (problemáticos em atom IMAP)
    assert "/" not in imap_keyword(LABEL_FOCO)
    assert " " not in imap_keyword(LABEL_FOCO)


# ---------- archive: predicado de "ainda na INBOX" e conjuntos ----------

def test_in_inbox_predicate():
    assert aa._in_inbox(SimpleNamespace(mailbox="INBOX", raw_labels=[], ai_labels=[])) is True
    assert aa._in_inbox(SimpleNamespace(mailbox="AI.Marketing", raw_labels=[], ai_labels=[])) is False
    # Gmail: INBOX vem em raw_labels
    assert aa._in_inbox(SimpleNamespace(mailbox="", raw_labels=["INBOX"], ai_labels=[])) is True


class _FolderClient:
    def __init__(self, folders, subscribed=()):
        self.folders = list(folders)
        self.subscribed = list(subscribed)
        self.created = []
        self.subscribed_names = []

    def list_folders(self):
        return self.folders

    def list_sub_folders(self):
        return self.subscribed

    def create_folder(self, name):
        self.created.append(name)
        self.folders.append(((), ".", name))

    def subscribe_folder(self, name):
        self.subscribed_names.append(name)


def test_archive_discovery_prefers_special_use_canary_name():
    client = _FolderClient([
        ((b"\\HasNoChildren",), ".", "Archive"),
        ((b"\\Archive",), ".", "Archives"),
    ])
    assert discover_folders(client)["archive"] == "Archives"
    assert ensure_archive_folder(client) == "Archives"
    assert client.created == []


def test_archive_discovery_creates_single_fallback():
    client = _FolderClient([((b"\\Inbox",), ".", "INBOX")])
    assert ensure_archive_folder(client) == "Archive"
    assert client.created == ["Archive"]
    assert client.subscribed_names == ["Archive"]


# ---------- CLI archive ----------

def test_cli_archive_rejects_bad_date():
    result = runner.invoke(cli.app, ["archive", "--before", "30/06/2026"])
    assert result.exit_code == 1
    assert "Data inválida" in result.output
