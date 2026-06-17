from types import SimpleNamespace

from email_agent.intelligence import ollama_client


def _settings(**over):
    base = dict(
        langfuse_enabled=False, langfuse_base_url="", llm_enabled=False,
        langfuse_public_key="", langfuse_secret_key="",
    )
    base.update(over)
    return SimpleNamespace(**base)


def test_langfuse_client_none_when_disabled(monkeypatch):
    ollama_client._langfuse_client.cache_clear()
    monkeypatch.setattr(ollama_client, "get_settings", lambda: _settings())
    assert ollama_client._langfuse_client() is None


def test_flush_langfuse_safe_when_no_client(monkeypatch):
    ollama_client._langfuse_client.cache_clear()
    monkeypatch.setattr(ollama_client, "get_settings", lambda: _settings())
    ollama_client.flush_langfuse()  # não deve levantar


def test_generate_json_none_when_llm_disabled(monkeypatch):
    monkeypatch.setattr(
        ollama_client, "get_settings",
        lambda: _settings(llm_enabled=False),
    )
    assert ollama_client.generate_json("oi") is None


def test_langfuse_client_registers_flush_atexit(monkeypatch):
    """Cliente criado deve registrar flush no atexit (causa raiz dos traces sumindo)."""
    ollama_client._langfuse_client.cache_clear()
    registered = []
    monkeypatch.setattr(ollama_client.atexit, "register", lambda fn: registered.append(fn))

    fake_client = SimpleNamespace(flush=lambda: None)
    monkeypatch.setattr(
        ollama_client, "get_settings",
        lambda: _settings(langfuse_enabled=True, langfuse_base_url="http://lf"),
    )

    class _FakeLangfuse:
        def __init__(self, **kwargs):
            pass

    import sys
    import types as _types
    fake_mod = _types.ModuleType("langfuse")
    fake_mod.Langfuse = lambda **kw: fake_client
    monkeypatch.setitem(sys.modules, "langfuse", fake_mod)

    client = ollama_client._langfuse_client()
    assert client is fake_client
    assert fake_client.flush in registered
    ollama_client._langfuse_client.cache_clear()
