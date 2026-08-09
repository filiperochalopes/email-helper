"""Política mover-não-copiar + AI/Archive."""
from types import SimpleNamespace

import pytest
from typer.testing import CliRunner

from email_agent.actions import archive_actions as aa
from email_agent.actions import gmail_actions, imap_actions, safety_gate
from email_agent.cli import app as cli
from email_agent.intelligence.taxonomy import (
    LABEL_AGUARDANDO,
    LABEL_ARCHIVE,
    LABEL_DOCUMENTOS,
    LABEL_FISCAL,
    LABEL_IMPORTANTE,
    LABEL_MARKETING,
    LABEL_SPAM_SUSPEITO,
    imap_destination,
    imap_keyword,
    moves_out_of_inbox,
)

runner = CliRunner()


# ---------- taxonomia: o que sai da INBOX e qual pasta IMAP vence ----------

@pytest.mark.parametrize("label,expected", [
    (LABEL_IMPORTANTE, False),
    (LABEL_AGUARDANDO, False),
    (LABEL_MARKETING, True),
    (LABEL_DOCUMENTOS, True),
    (LABEL_SPAM_SUSPEITO, True),
    (LABEL_ARCHIVE, True),
    ("AI/Inexistente", False),
])
def test_moves_out_of_inbox(label, expected):
    assert moves_out_of_inbox(label) is expected


def test_imap_destination_priority():
    # nenhuma label move -> fica na INBOX (None)
    assert imap_destination([LABEL_IMPORTANTE, LABEL_AGUARDANDO]) is None
    # mais específica/forte vence
    assert imap_destination([LABEL_DOCUMENTOS, LABEL_FISCAL]) == LABEL_FISCAL
    assert imap_destination([LABEL_MARKETING, LABEL_SPAM_SUSPEITO]) == LABEL_SPAM_SUSPEITO
    assert imap_destination([LABEL_MARKETING]) == LABEL_MARKETING


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

    safety_gate.apply_label(_account("gmail_api"), _msg(), LABEL_MARKETING)
    assert calls == [("move", LABEL_MARKETING)]


def test_apply_label_gmail_keep_only_adds(monkeypatch):
    calls = []
    monkeypatch.setattr(gmail_actions, "add_label", lambda *a: calls.append(("add", a[2])))
    monkeypatch.setattr(gmail_actions, "move_to_label", lambda *a: calls.append(("move", a[2])))

    safety_gate.apply_label(_account("gmail_api"), _msg(), LABEL_IMPORTANTE)
    assert calls == [("add", LABEL_IMPORTANTE)]


def test_apply_label_imap_move_updates_mailbox(monkeypatch):
    moved = []
    monkeypatch.setattr(imap_actions, "move_to_ai_folder",
                        lambda acc, m, lb: moved.append(lb) or "AI.Marketing")
    msg = _msg()
    safety_gate.apply_label(_account("imap"), msg, LABEL_MARKETING)
    assert moved == [LABEL_MARKETING]
    assert msg.mailbox == "AI.Marketing"


def test_apply_label_imap_keep_uses_keyword(monkeypatch):
    moved, kw = [], []
    monkeypatch.setattr(imap_actions, "move_to_ai_folder",
                        lambda acc, m, lb: moved.append(lb) or "AI.Importante")
    monkeypatch.setattr(imap_actions, "add_keyword",
                        lambda acc, m, lb: kw.append(lb) or True)
    msg = _msg()
    safety_gate.apply_label(_account("imap"), msg, LABEL_IMPORTANTE)
    assert moved == []                 # não cria pasta no IMAP para label que fica na INBOX
    assert kw == [LABEL_IMPORTANTE]    # vira keyword (etiqueta no lugar)
    assert msg.mailbox == "INBOX"


def test_imap_keyword_naming():
    assert imap_keyword(LABEL_IMPORTANTE) == "AI_Importante"
    assert imap_keyword(LABEL_AGUARDANDO) == "AI_Importante_Aguardando_Resposta"
    # sem '/' nem espaço (problemáticos em atom IMAP)
    assert "/" not in imap_keyword(LABEL_AGUARDANDO)
    assert " " not in imap_keyword(LABEL_AGUARDANDO)


# ---------- archive: predicado de "ainda na INBOX" e conjuntos ----------

def test_in_inbox_predicate():
    assert aa._in_inbox(SimpleNamespace(mailbox="INBOX", raw_labels=[], ai_labels=[])) is True
    assert aa._in_inbox(SimpleNamespace(mailbox="AI.Marketing", raw_labels=[], ai_labels=[])) is False
    # já arquivado nunca conta como inbox, mesmo que o mailbox esteja defasado
    assert aa._in_inbox(SimpleNamespace(mailbox="INBOX", raw_labels=["INBOX"],
                                        ai_labels=[LABEL_ARCHIVE])) is False
    # Gmail: INBOX vem em raw_labels
    assert aa._in_inbox(SimpleNamespace(mailbox="", raw_labels=["INBOX"], ai_labels=[])) is True


def test_auto_archive_label_set_is_strict():
    assert aa.AUTO_ARCHIVE_LABELS == {LABEL_IMPORTANTE, LABEL_DOCUMENTOS, LABEL_FISCAL}
    assert LABEL_MARKETING in aa.MANUAL_ARCHIVE_EXCLUDE
    assert LABEL_SPAM_SUSPEITO in aa.MANUAL_ARCHIVE_EXCLUDE


# ---------- CLI archive ----------

def test_cli_archive_rejects_bad_date():
    result = runner.invoke(cli.app, ["archive", "--before", "30/06/2026"])
    assert result.exit_code == 1
    assert "Data inválida" in result.output
