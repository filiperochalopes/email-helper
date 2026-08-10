from types import SimpleNamespace

from email_agent.intelligence import llm_client


def _settings(**over):
    base = {
        "langfuse_enabled": False, "langfuse_base_url": "", "llm_enabled": False,
        "langfuse_public_key": "", "langfuse_secret_key": "",
        "llm_provider": "disabled", "llm_base_url": "http://llm.test:11434",
        "llm_api_token": "", "llm_model": "modelo-local",
    }
    base.update(over)
    return SimpleNamespace(**base)


def test_langfuse_client_none_when_disabled(monkeypatch):
    llm_client._langfuse_client.cache_clear()
    monkeypatch.setattr(llm_client, "get_settings", lambda: _settings())
    assert llm_client._langfuse_client() is None


def test_flush_langfuse_safe_when_no_client(monkeypatch):
    llm_client._langfuse_client.cache_clear()
    monkeypatch.setattr(llm_client, "get_settings", lambda: _settings())
    llm_client.flush_langfuse()  # não deve levantar


def test_generate_json_none_when_llm_disabled(monkeypatch):
    monkeypatch.setattr(
        llm_client, "get_settings",
        lambda: _settings(llm_enabled=False),
    )
    result = llm_client.generate_json("oi")
    assert result.data is None
    assert result.error == "LLM desabilitada"


def test_generate_json_uses_ollama_http_api(monkeypatch):
    monkeypatch.setattr(
        llm_client,
        "get_settings",
        lambda: _settings(llm_enabled=True, llm_provider="ollama"),
    )
    monkeypatch.setattr(llm_client, "_langfuse_client", lambda: None)
    calls = []

    class _Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {"response": '{"category": "revisar"}', "prompt_eval_count": 4}

    def _post(url, **kwargs):
        calls.append((url, kwargs))
        return _Response()

    monkeypatch.setattr(llm_client.httpx, "post", _post)
    result = llm_client.generate_json("mensagem")
    assert result.data == {"category": "revisar"}
    assert result.provider == "ollama"
    assert result.input_tokens == 4
    assert calls[0][0] == "http://llm.test:11434/api/generate"
    assert calls[0][1]["json"]["stream"] is False
    assert calls[0][1]["json"]["format"] == "json"


def test_generate_json_uses_openai_compatible_api(monkeypatch):
    monkeypatch.setattr(
        llm_client,
        "get_settings",
        lambda: _settings(
            llm_enabled=True,
            llm_provider="openai_compatible",
            llm_base_url="https://llm.example.com",
            llm_api_token="token-teste",
        ),
    )
    monkeypatch.setattr(llm_client, "_langfuse_client", lambda: None)
    calls = []

    class _Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "choices": [{"message": {"content": '{"priority": "P1"}'}}],
                "usage": {"prompt_tokens": 8, "completion_tokens": 3},
            }

    def _post(url, **kwargs):
        calls.append((url, kwargs))
        return _Response()

    monkeypatch.setattr(llm_client.httpx, "post", _post)
    result = llm_client.generate_json("mensagem")
    assert result.data == {"priority": "P1"}
    assert result.input_tokens == 8
    assert calls[0][0] == "https://llm.example.com/v1/chat/completions"
    assert calls[0][1]["headers"] == {"Authorization": "Bearer token-teste"}


def test_langfuse_client_registers_flush_atexit(monkeypatch):
    """Cliente criado deve registrar flush no atexit (causa raiz dos traces sumindo)."""
    llm_client._langfuse_client.cache_clear()
    registered = []
    monkeypatch.setattr(llm_client.atexit, "register", lambda fn: registered.append(fn))

    fake_client = SimpleNamespace(flush=lambda: None)
    monkeypatch.setattr(
        llm_client, "get_settings",
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

    client = llm_client._langfuse_client()
    assert client is fake_client
    assert fake_client.flush in registered
    llm_client._langfuse_client.cache_clear()
