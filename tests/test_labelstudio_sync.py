from types import SimpleNamespace

from email_agent.labelstudio import sync
from email_agent.labelstudio.export import build_task_data


def _msg(eid="E-1", labels=None):
    return SimpleNamespace(
        email_agent_id=eid, account_id=1, mailbox="INBOX", from_name="Fulano",
        from_email="x@y.com", subject="Assunto", date=None, snippet="snip",
        normalized_text="corpo do e-mail", attachments=[], ai_labels=labels or [],
    )


def _cls(category="spam_suspeito", confidence=0.4, priority="ignore"):
    return SimpleNamespace(
        category=category, confidence=confidence, priority=priority,
        spam_score=0.8, importance_score=10.0, importance_reason="motivo",
    )


def test_build_task_data_has_id_and_suggestion():
    data = build_task_data(_msg("E-42"), _cls(category="marketing"))
    assert data["email_agent_id"] == "E-42"
    assert data["suggested_category"] == "marketing"
    assert data["text"] == "corpo do e-mail"


def test_prediction_maps_to_choices_control():
    pred = sync._prediction(_cls(category="documento_fiscal", confidence=0.9))
    assert pred["model_version"] == sync.MODEL_VERSION
    assert pred["score"] == 0.9
    result = pred["result"][0]
    assert result["from_name"] == "category"
    assert result["to_name"] == "body"
    assert result["value"]["choices"] == ["documento_fiscal"]


def test_extract_choice_from_dict_annotations():
    annotations = [{"result": [{"value": {"choices": ["importante_p1"]}}]}]
    assert sync._extract_choice(annotations) == "importante_p1"


def test_extract_choice_from_sdk_objects():
    ann = SimpleNamespace(result=[SimpleNamespace(value={"choices": ["revisar"]})])
    assert sync._extract_choice([ann]) == "revisar"


def test_extract_choice_none_when_empty():
    assert sync._extract_choice([]) is None
    assert sync._extract_choice([{"result": []}]) is None


def test_client_none_when_not_configured(monkeypatch):
    monkeypatch.setattr(
        sync, "get_settings",
        lambda: SimpleNamespace(label_studio_enabled=False),
    )
    assert sync._client() is None


def test_push_and_pull_noop_without_config(monkeypatch):
    monkeypatch.setattr(sync, "_client", lambda: None)
    assert sync.push_pending_tasks() == 0
    assert sync.pull_annotations() == 0
