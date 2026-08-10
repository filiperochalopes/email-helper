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
