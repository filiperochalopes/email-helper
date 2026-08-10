from sqlalchemy.dialects import postgresql

from email_agent.search import email_search_statement


def _sql(**kwargs) -> str:
    statement = email_search_statement(**kwargs)
    return str(statement.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}))


def test_text_search_combines_full_text_and_trigrams():
    sql = _sql(query="contrato filipe", limit=25)
    assert "websearch_to_tsquery" in sql
    assert "@@" in sql
    assert "%" in sql
    assert "ts_rank_cd" in sql
    assert "LIMIT 25" in sql


def test_search_filters_classification_and_caps_limit():
    sql = _sql(category="marketing", priority="P2", limit=50_000)
    assert "email_classification.category = 'marketing'" in sql
    assert "email_classification.priority = 'P2'" in sql
    assert "LIMIT 500" in sql
