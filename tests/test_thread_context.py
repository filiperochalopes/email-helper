from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import MagicMock

from email_agent.intelligence.thread_context import build_thread_context
from email_agent.sync.persist import _imap_thread_id, _update_thread_metadata


def _message(*, message_id, date, sent, body, thread="thread-1"):
    return SimpleNamespace(
        id=message_id,
        account_id=7,
        provider_thread_id=thread,
        date=date,
        is_sent_by_user=sent,
        from_email="me@example.com" if sent else "other@example.com",
        from_name="Eu" if sent else "Outra pessoa",
        subject="Assunto",
        normalized_text=body,
        snippet=body[:100],
    )


def test_thread_context_is_chronological_and_marks_pending_direction():
    first = _message(
        message_id=1,
        date=datetime(2026, 1, 1, tzinfo=UTC),
        sent=False,
        body="Você pode enviar o documento?",
    )
    reply = _message(
        message_id=2,
        date=datetime(2026, 1, 2, tzinfo=UTC),
        sent=True,
        body="Segue o documento.\n\nOn Jan 1, other wrote:\n> texto antigo",
    )
    session = MagicMock()
    session.execute.return_value.scalars.return_value = [reply, first]
    session.get.return_value = SimpleNamespace(email_address="me@example.com")

    context = build_thread_context(
        session,
        first,
        now=datetime(2026, 8, 13, tzinfo=UTC),
    )

    assert context.history.index("Você pode") < context.history.index("Segue o documento")
    assert "[MENSAGEM AVALIADA]" in context.history
    assert "Última mensagem conhecida: usuário → terceiro" in context.history
    assert "> texto antigo" not in context.history
    assert context.message_date.startswith("2026-01-01")


def test_thread_context_reports_when_thread_is_unavailable():
    message = _message(
        message_id=1,
        date=datetime(2026, 8, 1, tzinfo=UTC),
        sent=False,
        body="Mensagem isolada",
        thread=None,
    )
    context = build_thread_context(
        MagicMock(), message, now=datetime(2026, 8, 13, tzinfo=UTC)
    )
    assert "Histórico indisponível" in context.history
    assert "12 dias" in context.history


def test_imap_thread_id_uses_first_reference_as_root():
    parsed = SimpleNamespace(
        references=["<root@example.com>", "<parent@example.com>"],
        in_reply_to_header="<parent@example.com>",
        message_id_header="<reply@example.com>",
    )
    assert _imap_thread_id(parsed) == "imap:<root@example.com>"


def test_imap_reply_without_references_reuses_known_parent_thread():
    parsed = SimpleNamespace(
        references=[],
        in_reply_to_header="<parent@example.com>",
        message_id_header="<reply@example.com>",
    )
    session = MagicMock()
    session.execute.return_value.scalar_one_or_none.return_value = SimpleNamespace(
        provider_thread_id="imap:<root@example.com>"
    )
    assert _imap_thread_id(parsed, session, 7) == "imap:<root@example.com>"


def test_resync_backfills_thread_metadata_on_existing_imap_message():
    parsed = SimpleNamespace(
        references=["<root@example.com>"],
        in_reply_to_header="<parent@example.com>",
        message_id_header="<reply@example.com>",
    )
    message = SimpleNamespace(
        account_id=7,
        provider_thread_id=None,
        in_reply_to_header=None,
        references_json=None,
    )
    session = MagicMock()
    session.execute.return_value.scalar_one_or_none.return_value = None

    _update_thread_metadata(
        session,
        message,
        provider="imap",
        provider_thread_id=None,
        parsed=parsed,
    )

    assert message.provider_thread_id == "imap:<root@example.com>"
    assert message.in_reply_to_header == "<parent@example.com>"
    assert message.references_json == ["<root@example.com>"]
