from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import MagicMock

from email_agent.corpus.builder import (
    SOURCE_QUOTE,
    SOURCE_THREAD,
    build_example,
    export_jsonl,
)


def _message(*, message_id, date, sent, body, subject="Assunto", thread="thread-1"):
    return SimpleNamespace(
        id=message_id,
        email_agent_id=f"E-20260101-{message_id:06d}",
        account_id=7,
        provider_thread_id=thread,
        date=date,
        is_sent_by_user=sent,
        from_email="me@example.com" if sent else "outro@example.com",
        from_name="Eu" if sent else "Outra pessoa",
        subject=subject,
        to_json=["outro@example.com"] if sent else ["me@example.com"],
        normalized_text=body,
        snippet=body[:100],
    )


def _session_with(prior):
    session = MagicMock()
    session.execute.return_value.scalars.return_value = prior
    return session


def test_pair_uses_last_inbound_message_of_the_thread():
    inbound = _message(
        message_id=1,
        date=datetime(2026, 1, 1, tzinfo=UTC),
        sent=False,
        body="Consegue enviar a proposta até sexta?",
    )
    reply = _message(
        message_id=2,
        date=datetime(2026, 1, 2, tzinfo=UTC),
        sent=True,
        body="Claro, envio amanhã cedo.\n\nEm 1 de jan, Outra pessoa escreveu:\n> Consegue enviar",
    )

    example, reason = build_example(_session_with([inbound]), reply, "me@example.com")

    assert reason == "ok"
    assert example.incoming_source == SOURCE_THREAD
    assert example.incoming_text == "Consegue enviar a proposta até sexta?"
    assert example.reply_text == "Claro, envio amanhã cedo."
    assert example.account_email == "me@example.com"
    assert example.reply_id == "E-20260101-000002"


def test_context_never_contains_the_reply_itself():
    """O corte por data é o que impede o exemplo de trazer a própria resposta."""
    inbound = _message(
        message_id=1, date=datetime(2026, 1, 1, tzinfo=UTC), sent=False, body="Pergunta original"
    )
    reply = _message(
        message_id=2,
        date=datetime(2026, 1, 2, tzinfo=UTC),
        sent=True,
        body="Resposta secreta que o modelo deve produzir",
    )
    session = _session_with([inbound])

    example, _reason = build_example(session, reply, "me@example.com")

    serialized = str(example.history) + example.incoming_text
    assert "Resposta secreta" not in serialized
    assert [entry["direction"] for entry in example.history] == ["recebida"]


def test_quoted_block_recovers_incoming_when_original_is_missing():
    reply = _message(
        message_id=2,
        date=datetime(2026, 1, 2, tzinfo=UTC),
        sent=True,
        body=(
            "Segue o contrato revisado.\n\n"
            "Em 1 de jan, Outra pessoa escreveu:\n"
            "> Pode revisar o contrato?\n"
            "> Preciso até terça."
        ),
    )

    example, reason = build_example(_session_with([]), reply, "me@example.com")

    assert reason == "ok"
    assert example.incoming_source == SOURCE_QUOTE
    assert "Pode revisar o contrato?" in example.incoming_text
    assert "Preciso até terça." in example.incoming_text
    assert example.reply_text == "Segue o contrato revisado."


def test_cold_outbound_message_is_not_a_pair():
    reply = _message(
        message_id=2,
        date=datetime(2026, 1, 2, tzinfo=UTC),
        sent=True,
        body="Oi, tudo bem? Queria apresentar nosso produto.",
        thread=None,
    )

    example, reason = build_example(_session_with([]), reply, "me@example.com")

    assert example is None
    assert reason == "no_context"


def test_reply_without_own_text_is_discarded():
    reply = _message(
        message_id=2,
        date=datetime(2026, 1, 2, tzinfo=UTC),
        sent=True,
        body="> só a citação, nada escrito",
    )

    example, reason = build_example(_session_with([]), reply, "me@example.com")

    assert example is None
    assert reason == "empty_reply"


def test_reply_repeated_from_an_earlier_own_message_is_flagged():
    """Reenvio/template: o corte por data está certo, mas a resposta já está no
    contexto — uma métrica de otimização precisa poder excluir esses casos."""
    boilerplate = "Segue em anexo a documentação completa conforme combinado na reunião."
    earlier = _message(
        message_id=1, date=datetime(2026, 1, 1, tzinfo=UTC), sent=True, body=boilerplate
    )
    inbound = _message(
        message_id=2, date=datetime(2026, 1, 2, tzinfo=UTC), sent=False, body="Pode reenviar?"
    )
    reply = _message(
        message_id=3, date=datetime(2026, 1, 3, tzinfo=UTC), sent=True, body=boilerplate
    )

    example, _reason = build_example(_session_with([earlier, inbound]), reply, "me@example.com")

    assert example.self_overlap is True


def test_short_generic_reply_is_not_flagged_as_overlap():
    inbound = _message(
        message_id=1, date=datetime(2026, 1, 1, tzinfo=UTC), sent=False, body="Ok, obrigado!"
    )
    reply = _message(
        message_id=2, date=datetime(2026, 1, 2, tzinfo=UTC), sent=True, body="Ok, obrigado!"
    )

    example, _reason = build_example(_session_with([inbound]), reply, "me@example.com")

    assert example.self_overlap is False


def test_export_writes_one_json_object_per_line(tmp_path):
    inbound = _message(
        message_id=1, date=datetime(2026, 1, 1, tzinfo=UTC), sent=False, body="Pergunta"
    )
    reply = _message(
        message_id=2, date=datetime(2026, 1, 2, tzinfo=UTC), sent=True, body="Resposta"
    )
    example, _reason = build_example(_session_with([inbound]), reply, "me@example.com")

    destination = tmp_path / "sub" / "corpus.jsonl"
    assert export_jsonl([example, example], destination) == 2

    lines = destination.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 2
    assert '"reply_text": "Resposta"' in lines[0]
