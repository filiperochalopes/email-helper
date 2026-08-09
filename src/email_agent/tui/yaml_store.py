"""Leitura/escrita de secrets/accounts.yml e secrets/rules.yml preservando comentários.

Usa ruamel.yaml (round-trip) para não destruir comentários nem ordem quando o TUI
reescreve o arquivo. O YAML continua sendo a fonte de verdade — o banco é derivado
via `import-yaml`. Mantém o mesmo schema dos *.example.yml e dos importadores em
connectors/accounts_config.py e connectors/rules_config.py.
"""
from __future__ import annotations

import os
from pathlib import Path

from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap, CommentedSeq

_yaml = YAML()
_yaml.preserve_quotes = True
_yaml.indent(mapping=2, sequence=4, offset=2)


def _project_secrets_dir() -> Path:
    """Resolve a pasta secrets/ subindo a partir do cwd até achar pyproject.toml."""
    here = Path.cwd()
    for base in (here, *here.parents):
        if (base / "pyproject.toml").exists() and (base / "secrets").exists():
            return base / "secrets"
    return here / "secrets"


def accounts_path() -> Path:
    env = os.environ.get("ACCOUNTS_FILE")
    if env and not env.startswith("/secrets"):  # /secrets é path de container
        return Path(env)
    return _project_secrets_dir() / "accounts.yml"


def rules_path() -> Path:
    env = os.environ.get("RULES_FILE")
    if env and not env.startswith("/secrets"):
        return Path(env)
    return _project_secrets_dir() / "rules.yml"


class ValidationError(ValueError):
    """Erro de validação amigável, exibido na própria TUI."""


# ---------- contas ----------

def load_accounts(path: Path | None = None) -> CommentedMap:
    path = path or accounts_path()
    if not path.exists():
        data = CommentedMap()
        data["gmail"] = CommentedSeq()
        data["imap"] = CommentedSeq()
        return data
    with path.open() as f:
        data = _yaml.load(f) or CommentedMap()
    data.setdefault("gmail", CommentedSeq())
    data.setdefault("imap", CommentedSeq())
    return data


def save_accounts(data: CommentedMap, path: Path | None = None) -> Path:
    path = path or accounts_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as f:
        _yaml.dump(data, f)
    return path


def validate_gmail(entry: dict) -> None:
    if not (entry.get("email") or "").strip():
        raise ValidationError("Conta Gmail precisa de um e-mail.")


def validate_imap(entry: dict) -> None:
    for field in ("email", "host", "username", "password"):
        if not (str(entry.get(field) or "")).strip():
            raise ValidationError(f"Conta IMAP precisa do campo '{field}'.")
    port = entry.get("port")
    if port is not None:
        try:
            int(port)
        except (TypeError, ValueError) as exc:
            raise ValidationError("Porta IMAP deve ser um número.") from exc


def upsert_gmail(data: CommentedMap, email: str, display_name: str | None) -> None:
    entry = CommentedMap()
    entry["email"] = email.strip()
    if display_name and display_name.strip():
        entry["display_name"] = display_name.strip()
    validate_gmail(entry)
    _upsert_by_email(data["gmail"], entry)


def upsert_imap(data: CommentedMap, entry: dict) -> None:
    clean = CommentedMap()
    clean["email"] = (entry.get("email") or "").strip()
    if (entry.get("display_name") or "").strip():
        clean["display_name"] = entry["display_name"].strip()
    clean["host"] = (entry.get("host") or "").strip()
    clean["port"] = int(entry.get("port") or 993)
    clean["username"] = (entry.get("username") or "").strip()
    clean["password"] = entry.get("password") or ""
    validate_imap(clean)
    _upsert_by_email(data["imap"], clean)


def remove_account(data: CommentedMap, email: str) -> bool:
    for key in ("gmail", "imap"):
        seq = data.get(key) or []
        for i, e in enumerate(seq):
            if e.get("email") == email:
                del seq[i]
                return True
    return False


def _upsert_by_email(seq: CommentedSeq, entry: CommentedMap) -> None:
    for i, e in enumerate(seq):
        if e.get("email") == entry["email"]:
            seq[i] = entry
            return
    seq.append(entry)


# ---------- regras ----------

def load_rules(path: Path | None = None) -> CommentedMap:
    path = path or rules_path()
    if not path.exists():
        data = CommentedMap()
        data["rules"] = CommentedSeq()
        return data
    with path.open() as f:
        data = _yaml.load(f) or CommentedMap()
    data.setdefault("rules", CommentedSeq())
    return data


def save_rules(data: CommentedMap, path: Path | None = None) -> Path:
    path = path or rules_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as f:
        _yaml.dump(data, f)
    return path


_VALID_PRIORITIES = {"P0", "P1", "P2", "ignore", ""}


def validate_rule(entry: dict) -> None:
    if not (entry.get("name") or "").strip():
        raise ValidationError("Regra precisa de um nome único.")
    if not (entry.get("scope") or "").strip():
        raise ValidationError("Regra precisa de um escopo (e-mail da conta ou '*').")
    if not (entry.get("description") or "").strip():
        raise ValidationError("Regra precisa de uma descrição em pt-BR para o LLM.")
    outcome = entry.get("outcome") or {}
    if outcome.get("priority", "") not in _VALID_PRIORITIES:
        raise ValidationError("Prioridade deve ser P0, P1, P2, ignore ou vazio.")


def upsert_rule(data: CommentedMap, entry: dict) -> None:
    rule = CommentedMap()
    rule["name"] = (entry.get("name") or "").strip()
    rule["scope"] = (entry.get("scope") or "*").strip()
    rule["description"] = (entry.get("description") or "").strip()
    outcome = CommentedMap()
    raw = entry.get("outcome") or {}
    if (raw.get("priority") or "").strip():
        outcome["priority"] = raw["priority"].strip()
    if (raw.get("category") or "").strip():
        outcome["category"] = raw["category"].strip()
    labels = raw.get("labels")
    if labels:
        if isinstance(labels, str):
            labels = [x.strip() for x in labels.split(",") if x.strip()]
        outcome["labels"] = list(labels)
    rule["outcome"] = outcome
    validate_rule({**rule, "outcome": dict(outcome)})
    seq = data["rules"]
    for i, e in enumerate(seq):
        if e.get("name") == rule["name"]:
            seq[i] = rule
            return
    seq.append(rule)


def remove_rule(data: CommentedMap, name: str) -> bool:
    seq = data.get("rules") or []
    for i, e in enumerate(seq):
        if e.get("name") == name:
            del seq[i]
            return True
    return False


def spam_domain_rule(domain: str) -> dict:
    """Template de regra: marcar um domínio como spam suspeito (label AI/Spam Suspeito).

    Respeita a política do MVP: ação 'negativa' máxima é a label AI/Spam Suspeito,
    nunca deleção automática.
    """
    domain = domain.strip().lstrip("@").lower()
    return {
        "name": f"spam-dominio-{domain.replace('.', '-')}",
        "scope": "*",
        "description": (
            f"Mensagens cujo remetente é do domínio {domain} costumam ser spam/propaganda "
            f"não solicitada. Trate como spam suspeito, salvo se for uma resposta direta a "
            f"algo que eu enviei."
        ),
        "outcome": {"priority": "ignore", "labels": ["AI/Spam Suspeito"]},
    }
