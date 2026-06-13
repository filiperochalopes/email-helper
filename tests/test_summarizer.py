import pytest

from email_agent.intelligence.summarizer import _parse_json_response


def test_parse_plain_json():
    assert _parse_json_response('{"ok": true}') == {"ok": True}


def test_parse_markdown_fenced_json():
    assert _parse_json_response('```json\n{\n  "ok": true\n}\n```') == {"ok": True}


def test_parse_json_with_surrounding_text():
    assert _parse_json_response('Claro! Segue: {"resumo": "x"} espero ter ajudado') == {"resumo": "x"}


def test_parse_no_json_raises():
    with pytest.raises(ValueError):
        _parse_json_response("sem json aqui")
