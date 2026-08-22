from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import MagicMock

from email_agent.intelligence import composer


def _msg(mid, *, thread, date, sent=False, aid=None):
    return SimpleNamespace(
        id=mid,
        email_agent_id=aid or f"E-20260101-{mid:06d}",
        account_id=7,
        provider_thread_id=thread,
        date=date,
        is_sent_by_user=sent,
        from_email="outro@example.com",
        from_name="Outra pessoa",
        subject="Contrato",
        normalized_text="Pode confirmar o prazo?",
        snippet="Pode confirmar o prazo?",
    )


def _cls(cid, category=composer.TARGET_CATEGORY):
    return SimpleNamespace(id=cid, category=category)


def _session(rows):
    session = MagicMock()
    session.execute.return_value.all.return_value = rows
    return session


def test_one_draft_per_thread_not_per_message():
    """As pendências se concentram em poucas conversas; um rascunho por mensagem
    geraria uma pilha redundante para a mesma thread."""
    d1 = datetime(2026, 1, 1, tzinfo=UTC)
    d2 = datetime(2026, 1, 5, tzinfo=UTC)
    rows = [
        (_msg(1, thread="t-1", date=d1), _cls(10)),
        (_msg(2, thread="t-1", date=d2), _cls(11)),
        (_msg(3, thread="t-2", date=d1), _cls(12)),
    ]
    targets = composer.find_draft_targets(_session(rows))

    assert [m.id for m in targets] == [2, 3]  # a mais recente de cada thread


def test_message_without_thread_is_its_own_conversation():
    d = datetime(2026, 1, 1, tzinfo=UTC)
    rows = [(_msg(1, thread=None, date=d), _cls(10)), (_msg(2, thread=None, date=d), _cls(11))]
    assert len(composer.find_draft_targets(_session(rows))) == 2


def test_superseded_classification_is_ignored():
    """Se a classificação mais recente saiu da categoria, a conversa não é alvo."""
    d = datetime(2026, 1, 1, tzinfo=UTC)
    message = _msg(1, thread="t-1", date=d)
    rows = [(message, _cls(10)), (message, _cls(99, category="ignorar"))]
    assert composer.find_draft_targets(_session(rows)) == []


def test_limit_is_respected():
    d = datetime(2026, 1, 1, tzinfo=UTC)
    rows = [(_msg(i, thread=f"t-{i}", date=d), _cls(i)) for i in range(1, 6)]
    assert len(composer.find_draft_targets(_session(rows), limit=2)) == 2


def test_style_card_falls_back_to_global(tmp_path):
    (tmp_path / "_global.md").write_text("cartão geral", encoding="utf-8")
    text, source = composer.load_style_card("sem-cartao@x.com", tmp_path)
    assert text == "cartão geral"
    assert source == composer.GLOBAL_CARD_NAME


def test_own_style_card_wins_over_global(tmp_path):
    (tmp_path / "_global.md").write_text("geral", encoding="utf-8")
    (tmp_path / "dono@x.com.md").write_text("meu", encoding="utf-8")
    text, source = composer.load_style_card("dono@x.com", tmp_path)
    assert (text, source) == ("meu", "dono@x.com")


def test_missing_cards_are_reported_not_crashing(tmp_path):
    assert composer.load_style_card("x@y.com", tmp_path) == ("", "nenhum")


def _compose(monkeypatch, tmp_path, data, error=None):
    monkeypatch.setattr(
        composer, "generate_json",
        lambda prompt, **kw: SimpleNamespace(
            data=data, error=error, model="gemma4:e4b-mlx"
        ),
    )
    monkeypatch.setattr(
        composer, "build_thread_context",
        lambda *a, **k: SimpleNamespace(history="histórico", message_date="", current_date=""),
    )
    session = MagicMock()
    session.get.return_value = SimpleNamespace(email_address="dono@x.com")
    message = _msg(1, thread="t-1", date=datetime(2026, 1, 1, tzinfo=UTC))
    return composer.compose_draft(session, message, tmp_path)


def test_draft_carries_pending_items_and_confidence(monkeypatch, tmp_path):
    draft = _compose(monkeypatch, tmp_path, {
        "assunto": "Re: Contrato",
        "corpo": "Confirmo para [CONFIRMAR DATA].",
        "confianca": 0.55,
        "pendencias": ["data exata do prazo"],
    })
    assert draft.subject == "Re: Contrato"
    assert draft.confidence == 0.55
    assert draft.pending == ["data exata do prazo"]
    assert draft.model == "gemma4:e4b-mlx"
    assert draft.error is None


def test_subject_gets_re_prefix_when_model_omits_it(monkeypatch, tmp_path):
    draft = _compose(monkeypatch, tmp_path, {"corpo": "ok"})
    assert draft.subject == "Re: Contrato"


def test_llm_failure_produces_a_draft_marked_with_error(monkeypatch, tmp_path):
    draft = _compose(monkeypatch, tmp_path, None, error="timed out")
    assert draft.error == "timed out"
    assert draft.body == ""


def test_persisted_draft_only_touches_human_review(monkeypatch, tmp_path):
    draft = _compose(monkeypatch, tmp_path, {"corpo": "texto", "confianca": 0.8})
    session = MagicMock()
    review = composer.persist_draft(session, draft)

    assert review.review_type == composer.DRAFT_REVIEW_TYPE
    assert review.status == "pending"
    assert review.proposed_action_json["body"] == "texto"
    assert review.proposed_action_json["prompt_version"] == composer.COMPOSE_PROMPT_VERSION
    session.add.assert_called_once_with(review)
