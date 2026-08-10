"""Carrega regras de importância de secrets/rules.yml para a tabela email_rule.

Formato (ver rules.example.yml):
rules:
  - name: identificador-unico
    scope: conta@dominio.com   # ou "*" para todas as contas
    description: >
      Texto em pt-BR que o agente LLM usa para decidir se a regra se aplica,
      incluindo exceções.
    outcome:
      priority: P0             # P0|P1|P2|ignore (opcional)
      category: documento_fiscal   # categoria interna (opcional)
      labels: [AI/Foco]  # labels AI a sugerir (opcional)
"""
import os

import yaml
from sqlalchemy import select

from email_agent.logging_setup import get_logger
from email_agent.models import EmailRule, db_session

log = get_logger(__name__)

RULES_FILE = os.environ.get("RULES_FILE", "/secrets/rules.yml")


def import_rules(path: str | None = None) -> dict[str, int]:
    path = path or RULES_FILE
    if not os.path.exists(path):
        raise FileNotFoundError(
            f"{path} não encontrado. Copie secrets/rules.example.yml para secrets/rules.yml."
        )
    with open(path) as f:
        data = yaml.safe_load(f) or {}

    declared: set[str] = set()
    created = updated = 0
    with db_session() as session:
        for entry in data.get("rules", []):
            name = entry["name"]
            declared.add(name)
            rule = session.execute(
                select(EmailRule).where(EmailRule.name == name)
            ).scalar_one_or_none()
            if rule is None:
                rule = EmailRule(name=name, rule_type="importance", created_by="user")
                session.add(rule)
                created += 1
            else:
                updated += 1
            rule.rule_type = "importance"
            rule.condition_json = {
                "scope": entry.get("scope", "*"),
                "description": entry.get("description", "").strip(),
            }
            rule.action_json = entry.get("outcome", {})
            rule.is_active = entry.get("active", True)

        # regras importadas que sumiram do YAML => desativa (não apaga)
        deactivated = 0
        for rule in session.execute(
            select(EmailRule).where(EmailRule.rule_type == "importance", EmailRule.created_by == "user")
        ).scalars():
            if rule.name not in declared and rule.is_active:
                rule.is_active = False
                deactivated += 1

    log.info("rules_imported", created=created, updated=updated, deactivated=deactivated)
    return {"created": created, "updated": updated, "deactivated": deactivated}
