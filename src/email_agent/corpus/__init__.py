"""Catálogo de respostas do usuário, base para a composição assistida."""
from email_agent.corpus.builder import (
    CorpusStats,
    ReplyExample,
    collect_reply_examples,
    export_jsonl,
)

__all__ = ["CorpusStats", "ReplyExample", "collect_reply_examples", "export_jsonl"]
