from contextlib import nullcontext
from types import SimpleNamespace

import pytest

from email_agent.intelligence import graph, triage
from email_agent.intelligence.graph import apply_rules
from email_agent.intelligence.llm_client import LLMCallResult
from email_agent.intelligence.rule_agent import match_spam_rule
from email_agent.intelligence.triage import normalize_triage


def _result(**changes):
    data = {
        "category": "marketing",
        "priority": "ignore",
        "action_required": False,
        "cleanup_candidate": True,
        "cleanup_reason": "newsletter promocional",
        "spam_score": 0.1,
        "importance_score": 5,
        "confidence": 0.92,
        "summary": "Oferta comercial.",
        "reason": "Sem ação necessária.",
    }
    data.update(changes)
    return normalize_triage(data)


def test_conservative_cleanup_candidate_is_preserved():
    result = _result()
    assert result.cleanup_candidate is True
    assert result.category == "marketing"


def test_relevant_category_can_never_be_preselected_for_cleanup():
    result = _result(category="documento_fiscal", cleanup_candidate=True)
    assert result.cleanup_candidate is False


def test_low_confidence_never_preselects_cleanup():
    result = _result(confidence=0.2, cleanup_candidate=True)
    assert result.needs_human_review is True
    assert result.cleanup_candidate is False


def test_invalid_llm_response_fails_to_review():
    result = normalize_triage(None)
    assert result.category == "revisar"
    assert result.cleanup_candidate is False
    assert result.needs_human_review is True


def test_triage_carries_llm_audit_metadata(monkeypatch):
    payload = {
        "category": "marketing", "priority": "P2", "confidence": 0.9,
        "cleanup_candidate": True, "summary": "Oferta", "reason": "Marketing",
    }
    monkeypatch.setattr(
        triage,
        "generate_json",
        lambda *a, **k: LLMCallResult(
            payload, "openai_compatible", "modelo-x", raw_response='{"category":"marketing"}',
            input_tokens=100, output_tokens=20, latency_ms=321,
        ),
    )
    monkeypatch.setattr(
        triage,
        "get_settings",
        lambda: SimpleNamespace(max_email_text_chars=1000, llm_min_confidence=0.6),
    )
    result = triage.triage_email(
        account_email="conta@example.com", mailbox="INBOX", from_email="loja@example.com",
        from_name="Loja", subject="Oferta", body="Promoção", attachments=[],
        in_provider_spam=False, is_sent_by_user=False,
    )
    assert result.llm_provider == "openai_compatible"
    assert result.llm_model == "modelo-x"
    assert result.llm_raw_result == payload
    assert result.llm_input_tokens == 100
    assert result.llm_latency_ms == 321


def test_spam_blacklist_matches_sender_and_domain_without_content_evaluation():
    sender_rule = SimpleNamespace(
        condition_json={"match": {"sender": "blocked@example.com"}}
    )
    domain_rule = SimpleNamespace(
        condition_json={"match": {"domain": "marketing.example"}}
    )
    assert match_spam_rule("BLOCKED@example.com", [sender_rule]) is sender_rule
    assert match_spam_rule("news@sub.marketing.example", [domain_rule]) is domain_rule
    assert match_spam_rule("news@notmarketing.example", [domain_rule]) is None


def test_spam_blacklist_prevents_later_importance_evaluation():
    assert apply_rules({"blacklist_matched": True}) == {}


def test_spam_blacklist_becomes_cleanup_suggestion_without_calling_llm(monkeypatch):
    rule = SimpleNamespace(
        name="spam-domain-example",
        condition_json={"match": {"domain": "marketing.example"}},
        action_json={"labels": ["AI/Spam Suspeito"]},
    )
    monkeypatch.setattr(graph, "db_session", lambda: nullcontext(None))
    monkeypatch.setattr(graph, "load_spam_rules_for_account", lambda *_: [rule])
    monkeypatch.setattr(graph, "triage_email", lambda **_: pytest.fail("LLM não deve ser chamada"))

    result = graph.classify_message({
        "account_email": "me@example.com",
        "from_email": "news@marketing.example",
        "is_sent_by_user": False,
    })

    assert result["cleanup_candidate"] is True
    assert "blacklist" in result["cleanup_reason"].lower()
