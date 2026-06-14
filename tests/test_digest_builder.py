from datetime import date
from types import SimpleNamespace

from email_agent.digest import builder

_DAY = date(2026, 6, 13)


def _msg(eid, subject="Assunto", sender="Fulano", snippet="snippet"):
    return SimpleNamespace(
        email_agent_id=eid, subject=subject, from_name=sender, from_email="x@y.com",
        snippet=snippet, ai_labels=[],
    )


def _cls(summary="resumo", priority="P0", category="noticia"):
    return SimpleNamespace(digest_summary=summary, priority=priority, category=category)


def test_main_lists_ids_in_overflow():
    pairs = [(_msg(f"E-{i}"), _cls()) for i in range(builder.MAX_ACTION_ITEMS + 3)]
    st = builder._Stats(total=20, contas=3)
    text = builder._build_main(_DAY, pairs, [], [], st)
    # os 3 itens que estouram o cap devem aparecer listados por id
    overflow_ids = [m.email_agent_id for m, _ in pairs[builder.MAX_ACTION_ITEMS:]]
    assert "no app:" in text
    for eid in overflow_ids:
        assert eid in text


def test_main_reminds_show_command():
    pairs = [(_msg("E-1"), _cls())]
    st = builder._Stats(total=1, contas=1)
    text = builder._build_main(_DAY, pairs, [], [], st)
    assert "show <ID>" in text
    assert builder.DOCKER in text


def test_cleanup_message_has_delete_hint_and_ids():
    pairs = [(_msg("E-10"), _cls(category="marketing")), (_msg("E-11"), _cls(category="promocao"))]
    text = builder._build_cleanup(pairs)
    assert "Candidatos a exclusão" in text
    assert "E-10" in text and "E-11" in text
    # dica de exclusão com docker e os ids
    assert f"{builder.DOCKER} delete" in text


def test_news_footer_uses_docker_exec_it():
    pairs = [(_msg("E-1"), _cls(category="noticia"))]
    text = builder._build_news(pairs)
    assert "docker compose exec -it app email-agent feedback <ID>" in text
