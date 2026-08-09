import pytest

from email_agent.tui import yaml_store as ys


def test_accounts_roundtrip_preserves_comments(tmp_path):
    path = tmp_path / "accounts.yml"
    path.write_text(
        "# comentário importante\n"
        "gmail:\n"
        "  - email: voce@gmail.com\n"
        "    display_name: Pessoal\n"
        "imap: []\n"
    )
    data = ys.load_accounts(path)
    ys.upsert_gmail(data, "novo@gmail.com", "Trabalho")
    ys.save_accounts(data, path)

    text = path.read_text()
    assert "# comentário importante" in text
    assert "novo@gmail.com" in text
    # entrada original preservada
    assert "voce@gmail.com" in text


def test_upsert_gmail_updates_existing(tmp_path):
    data = ys.load_accounts(tmp_path / "x.yml")
    ys.upsert_gmail(data, "a@gmail.com", "Um")
    ys.upsert_gmail(data, "a@gmail.com", "Dois")
    assert len(data["gmail"]) == 1
    assert data["gmail"][0]["display_name"] == "Dois"


def test_imap_validation_requires_fields(tmp_path):
    data = ys.load_accounts(tmp_path / "x.yml")
    with pytest.raises(ys.ValidationError):
        ys.upsert_imap(data, {"email": "a@b.com", "host": "", "username": "a", "password": "x"})


def test_imap_default_port_993(tmp_path):
    data = ys.load_accounts(tmp_path / "x.yml")
    ys.upsert_imap(data, {"email": "a@b.com", "host": "mail.b.com", "username": "a", "password": "x", "port": ""})
    assert data["imap"][0]["port"] == 993


def test_remove_account(tmp_path):
    data = ys.load_accounts(tmp_path / "x.yml")
    ys.upsert_gmail(data, "a@gmail.com", "")
    assert ys.remove_account(data, "a@gmail.com") is True
    assert ys.remove_account(data, "naoexiste@x.com") is False


def test_rule_validation_and_labels_split(tmp_path):
    data = ys.load_rules(tmp_path / "r.yml")
    ys.upsert_rule(data, {
        "name": "vip", "scope": "*", "description": "fornecedor X é P0",
        "outcome": {"priority": "P0", "labels": "AI/Importante, AI/Fiscal"},
    })
    rule = data["rules"][0]
    assert rule["outcome"]["labels"] == ["AI/Importante", "AI/Fiscal"]


def test_rule_invalid_priority(tmp_path):
    data = ys.load_rules(tmp_path / "r.yml")
    with pytest.raises(ys.ValidationError):
        ys.upsert_rule(data, {
            "name": "x", "scope": "*", "description": "y", "outcome": {"priority": "URGENTE"},
        })


def test_spam_domain_rule_is_label_only():
    rule = ys.spam_domain_rule("@Promo.Exemplo.com")
    assert rule["name"] == "spam-dominio-promo-exemplo-com"
    assert rule["outcome"]["labels"] == ["AI/Spam Suspeito"]
    # política do MVP: nunca deleção automática
    assert rule["outcome"]["priority"] == "ignore"
