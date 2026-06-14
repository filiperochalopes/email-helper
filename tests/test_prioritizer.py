from types import SimpleNamespace

from email_agent.intelligence import prioritizer


def _pair(eid, score=0):
    msg = SimpleNamespace(
        email_agent_id=eid, date=None, from_email="x@y.com", subject="s", snippet="snip",
    )
    cls = SimpleNamespace(importance_score=score, digest_summary="resumo")
    return (msg, cls)


def test_apply_order_reorders_by_given_ids():
    a, b, c = _pair("E-1"), _pair("E-2"), _pair("E-3")
    out = prioritizer._apply_order([a, b, c], ["E-3", "E-1", "E-2"])
    assert [m.email_agent_id for m, _ in out] == ["E-3", "E-1", "E-2"]


def test_apply_order_unknown_ids_ignored_and_missing_appended():
    a, b, c = _pair("E-1"), _pair("E-2"), _pair("E-3")
    # ordena só E-3; cita um id inexistente; E-1/E-2 mantêm ordem original ao final
    out = prioritizer._apply_order([a, b, c], ["E-99", "E-3"])
    assert [m.email_agent_id for m, _ in out] == ["E-3", "E-1", "E-2"]


def test_apply_order_dedups_repeated_ids():
    a, b = _pair("E-1"), _pair("E-2")
    out = prioritizer._apply_order([a, b], ["E-1", "E-1", "E-2"])
    assert [m.email_agent_id for m, _ in out] == ["E-1", "E-2"]


def test_prioritize_p0_falls_back_when_llm_unavailable(monkeypatch):
    pairs = [_pair("E-1", 10), _pair("E-2", 90), _pair("E-3", 50)]
    monkeypatch.setattr(prioritizer, "generate_json", lambda *a, **k: None)
    assert prioritizer.prioritize_p0(pairs) == pairs


def test_prioritize_p0_applies_llm_order(monkeypatch):
    pairs = [_pair("E-1"), _pair("E-2"), _pair("E-3")]
    monkeypatch.setattr(
        prioritizer, "generate_json", lambda *a, **k: {"order": ["E-2", "E-3", "E-1"]}
    )
    out = prioritizer.prioritize_p0(pairs)
    assert [m.email_agent_id for m, _ in out] == ["E-2", "E-3", "E-1"]


def test_prioritize_p0_single_item_skips_llm(monkeypatch):
    called = {"n": 0}

    def _boom(*a, **k):
        called["n"] += 1
        return {"order": []}

    monkeypatch.setattr(prioritizer, "generate_json", _boom)
    pairs = [_pair("E-1")]
    assert prioritizer.prioritize_p0(pairs) == pairs
    assert called["n"] == 0
