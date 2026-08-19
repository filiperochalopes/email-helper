from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import MagicMock

from email_agent.intelligence.followup import (
    detect_awaiting_my_reply,
    detect_followup_waiting_response,
)


def _msg(*, sent, body="Pode confirmar o prazo?", thread="t-1", date=datetime(2026, 1, 2, tzinfo=UTC)):
    return SimpleNamespace(
        id=1,
        account_id=7,
        provider_thread_id=thread,
        date=date,
        is_sent_by_user=sent,
        from_email="me@example.com" if sent else "outro@example.com",
        subject="Assunto",
        normalized_text=body,
    )


def _session(later=None):
    session = MagicMock()
    session.execute.return_value.first.return_value = later
    session.get.return_value = SimpleNamespace(email_address="me@example.com")
    return session


def test_inbound_without_my_later_reply_awaits_me():
    ok, reason = detect_awaiting_my_reply(_session(None), _msg(sent=False))
    assert ok is True
    assert "sem resposta minha" in reason


def test_inbound_already_answered_by_me_does_not_await_me():
    ok, _ = detect_awaiting_my_reply(_session(later=("qualquer",)), _msg(sent=False))
    assert ok is False


def test_my_own_message_is_never_awaiting_my_reply():
    """A direção oposta é coberta por detect_followup_waiting_response."""
    ok, _ = detect_awaiting_my_reply(_session(None), _msg(sent=True))
    assert ok is False


def test_inbound_without_thread_is_not_classified():
    ok, _ = detect_awaiting_my_reply(_session(None), _msg(sent=False, thread=None))
    assert ok is False


def test_inbound_without_date_is_not_classified():
    ok, _ = detect_awaiting_my_reply(_session(None), _msg(sent=False, date=None))
    assert ok is False


def test_the_two_directions_are_mutually_exclusive():
    inbound, sent = _msg(sent=False), _msg(sent=True)
    assert detect_awaiting_my_reply(_session(None), inbound)[0] is True
    assert detect_followup_waiting_response(_session(None), inbound)[0] is False
    assert detect_awaiting_my_reply(_session(None), sent)[0] is False
    assert detect_followup_waiting_response(_session(None), sent)[0] is True
