from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from email_agent.api import cleanup


def test_bulk_action_requires_explicit_confirmation():
    request = cleanup.BulkActionRequest(
        action="trash", ids=["E-20260810-000001"]
    )

    with pytest.raises(HTTPException) as exc_info:
        cleanup.bulk_action(request)
    assert "confirmação explícita" in exc_info.value.detail


def test_bulk_action_deduplicates_and_isolates_failures(monkeypatch):
    calls = []

    def fake_trash(email_agent_id):
        calls.append(email_agent_id)
        return "trashed" if email_agent_id.endswith("1") else "error: provedor indisponível"

    monkeypatch.setattr(cleanup, "trash_message", fake_trash)
    response = cleanup.bulk_action(
        cleanup.BulkActionRequest(
            action="trash",
            ids=["E-1", "E-2", "E-1"],
            confirmed=True,
        )
    )

    assert calls == ["E-1", "E-2"]
    assert response["succeeded"] == 1
    assert response["failed"] == 1


def test_candidate_payload_never_exposes_full_body():
    message = SimpleNamespace(
        email_agent_id="E-1",
        from_email="news@example.com",
        from_name="Newsletter",
        subject="Resumo semanal",
        snippet="Uma prévia curta",
        normalized_text="corpo completo que não deve sair",
        date=None,
        is_read=False,
        has_attachment=False,
    )
    classification = SimpleNamespace(
        category="marketing",
        priority="ignore",
        cleanup_candidate=True,
        cleanup_reason="newsletter recorrente",
        confidence=0.94,
    )
    account = SimpleNamespace(email_address="me@example.com", provider="imap")

    payload = cleanup._candidate_payload(message, classification, account)

    assert payload["snippet"] == "Uma prévia curta"
    assert "normalized_text" not in payload
    assert "body" not in payload
