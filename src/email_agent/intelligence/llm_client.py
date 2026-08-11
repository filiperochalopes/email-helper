"""Adaptador agnóstico para geração estruturada por LLM.

Suporta Ollama e qualquer API compatível com OpenAI. A aplicação recebe sempre
o mesmo resultado tipado, incluindo métricas que serão persistidas junto à
classificação de cada e-mail.
"""
import atexit
import json
import re
from dataclasses import dataclass
from functools import lru_cache
from time import perf_counter

import httpx

from email_agent.config import get_settings
from email_agent.logging_setup import get_logger

log = get_logger(__name__)


@dataclass(frozen=True)
class LLMCallResult:
    data: dict | None
    provider: str
    model: str
    raw_response: str = ""
    input_tokens: int | None = None
    output_tokens: int | None = None
    latency_ms: int | None = None
    error: str | None = None


@lru_cache(maxsize=1)
def _langfuse_client():
    """Retorna o Langfuse somente quando as duas chaves estiverem configuradas."""
    settings = get_settings()
    if not settings.langfuse_enabled:
        return None
    try:
        from langfuse import Langfuse

        kwargs = {
            "public_key": settings.langfuse_public_key,
            "secret_key": settings.langfuse_secret_key,
        }
        if settings.langfuse_base_url:
            kwargs["base_url"] = settings.langfuse_base_url
        client = Langfuse(**kwargs)
        atexit.register(client.flush)
        return client
    except Exception as exc:  # noqa: BLE001
        log.warning("langfuse_init_failed", error=str(exc))
        return None


def flush_langfuse() -> None:
    client = _langfuse_client()
    if client:
        try:
            client.flush()
        except Exception as exc:  # noqa: BLE001
            log.debug("langfuse_flush_failed", error=str(exc))


def parse_json_response(text: str) -> dict:
    """Extrai o primeiro objeto JSON, tolerando cercas Markdown e texto ao redor."""
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        raise ValueError(f"resposta sem JSON: {text[:120]!r}")
    return json.loads(match.group(0))


def _ollama_call(
    prompt: str, *, base_url: str, api_token: str, model: str,
    temperature: float, timeout: float,
) -> tuple[str, int | None, int | None]:
    headers = {"Authorization": f"Bearer {api_token}"} if api_token else {}
    response = httpx.post(
        f"{base_url.rstrip('/')}/api/generate",
        headers=headers,
        json={
            "model": model,
            "prompt": prompt,
            "format": "json",
            "stream": False,
            "options": {"temperature": temperature},
        },
        timeout=timeout,
    )
    response.raise_for_status()
    payload = response.json()
    return (
        str(payload.get("response") or ""),
        payload.get("prompt_eval_count"),
        payload.get("eval_count"),
    )


def _openai_compatible_call(
    prompt: str, *, base_url: str, api_token: str, model: str,
    temperature: float, timeout: float,
) -> tuple[str, int | None, int | None]:
    headers = {"Authorization": f"Bearer {api_token}"} if api_token else {}
    root = base_url.rstrip("/")
    api_root = root if root.endswith("/v1") else f"{root}/v1"
    response = httpx.post(
        f"{api_root}/chat/completions",
        headers=headers,
        json={
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "response_format": {"type": "json_object"},
            "temperature": temperature,
            "stream": False,
        },
        timeout=timeout,
    )
    response.raise_for_status()
    payload = response.json()
    content = payload["choices"][0]["message"]["content"]
    usage = payload.get("usage") or {}
    return str(content or ""), usage.get("prompt_tokens"), usage.get("completion_tokens")


def generate_json(
    prompt: str,
    *,
    task: str = "base",
    temperature: float = 0.0,
    timeout: float = 120,
    trace_name: str | None = None,
    trace_metadata: dict | None = None,
) -> LLMCallResult:
    """Gera JSON pelo provider configurado sem expor detalhes ao restante do código."""
    settings = get_settings()
    provider = settings.llm_provider.strip().lower()
    model = settings.llm_model
    if not settings.llm_enabled:
        return LLMCallResult(None, provider or "disabled", model, error="LLM desabilitada")

    client = _langfuse_client()
    observation = None
    if client:
        try:
            observation = client.start_observation(
                as_type="generation",
                name=trace_name or f"llm-{task}",
                model=model,
                model_parameters={"temperature": temperature, "provider": provider},
                input=prompt,
                metadata=trace_metadata,
            )
        except Exception as exc:  # noqa: BLE001
            log.debug("langfuse_generation_start_failed", error=str(exc))

    started = perf_counter()
    try:
        if provider == "ollama":
            raw, input_tokens, output_tokens = _ollama_call(
                prompt, base_url=settings.llm_base_url, api_token=settings.llm_api_token,
                model=model,
                temperature=temperature, timeout=timeout,
            )
        elif provider in {"openai", "openai_compatible"}:
            raw, input_tokens, output_tokens = _openai_compatible_call(
                prompt, base_url=settings.llm_base_url, api_token=settings.llm_api_token,
                model=model, temperature=temperature, timeout=timeout,
            )
        else:
            raise ValueError(f"LLM_PROVIDER não suportado: {provider}")

        latency_ms = round((perf_counter() - started) * 1000)
        data = parse_json_response(raw)
        result = LLMCallResult(
            data, provider, model, raw[:20_000], input_tokens, output_tokens, latency_ms,
        )
        if observation:
            try:
                observation.update(
                    output=data,
                    usage_details={"input": input_tokens or 0, "output": output_tokens or 0},
                )
                observation.end()
            except Exception as exc:  # noqa: BLE001
                log.debug("langfuse_generation_end_failed", error=str(exc))
        return result
    except Exception as exc:  # noqa: BLE001
        latency_ms = round((perf_counter() - started) * 1000)
        error = str(exc)[:2_000]
        log.warning("llm_generate_failed", task=task, provider=provider, model=model, error=error)
        if observation:
            try:
                observation.update(level="ERROR", status_message=error)
                observation.end()
            except Exception as trace_exc:  # noqa: BLE001
                log.debug("langfuse_generation_error_failed", error=str(trace_exc))
        return LLMCallResult(None, provider, model, latency_ms=latency_ms, error=error)
