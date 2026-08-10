"""Cliente Ollama local centralizado.

Uma única função para todas as chamadas ao Ollama, com seleção de modelo por
task (`base` x `reasoning`). A inferência roda no Ollama local. Quando Langfuse
é habilitado explicitamente, prompts e respostas são exportados para a instância
configurada; sem as chaves, nenhuma telemetria externa é iniciada.
"""
import atexit
import json
import re
from functools import lru_cache

import httpx

from email_agent.config import get_settings
from email_agent.logging_setup import get_logger

log = get_logger(__name__)


@lru_cache(maxsize=1)
def _langfuse_client():
    """Retorna cliente Langfuse ou None se não configurado.

    Lê LANGFUSE_PUBLIC_KEY, LANGFUSE_SECRET_KEY e LANGFUSE_BASE_URL do env
    (o SDK v4 os consome diretamente). Retorna None se as chaves não estiverem
    definidas para não bloquear execução em ambientes sem Langfuse.
    """
    settings = get_settings()
    if not settings.langfuse_enabled:
        return None
    try:
        from langfuse import Langfuse
        kwargs = {}
        if settings.langfuse_base_url:
            kwargs["host"] = settings.langfuse_base_url
        client = Langfuse(**kwargs)
        # O SDK v4 envia eventos em batch num thread de background; processos
        # curtos (CLI e `email-agent run`) podem terminar antes do
        # batch sair. Sem este flush no encerramento os traces somem silenciosamente.
        atexit.register(client.flush)
        return client
    except Exception as exc:  # noqa: BLE001
        log.warning("langfuse_init_failed", error=str(exc))
        return None


def flush_langfuse() -> None:
    """Força o envio dos eventos pendentes ao Langfuse (chamar ao fim de jobs)."""
    lf = _langfuse_client()
    if lf:
        try:
            lf.flush()
        except Exception as exc:  # noqa: BLE001
            log.debug("langfuse_flush_failed", error=str(exc))


def parse_json_response(text: str) -> dict:
    """Extrai o primeiro objeto JSON da resposta.

    Alguns modelos (ex.: builds MLX) ignoram format=json e devolvem o JSON
    dentro de cerca markdown ou com texto ao redor.
    """
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        raise ValueError(f"resposta sem JSON: {text[:120]!r}")
    return json.loads(match.group(0))


def generate_json(
    prompt: str,
    *,
    task: str = "base",
    temperature: float = 0.0,
    timeout: float = 120,
    trace_name: str | None = None,
    trace_metadata: dict | None = None,
) -> dict | None:
    """Chama o Ollama em modo JSON e devolve o dict, ou None em falha/desligado.

    trace_name: nome semântico para o trace do Langfuse (ex.: "classify", "summarize").
    trace_metadata: dict extra para o trace (ex.: account_id, email_id).
    """
    settings = get_settings()
    if not settings.llm_enabled:
        return None
    model = settings.model_for(task)
    lf = _langfuse_client()
    obs = None
    if lf:
        try:
            obs = lf.start_observation(
                as_type="generation",
                name=trace_name or f"ollama-{task}",
                model=model,
                model_parameters={"temperature": temperature},
                input=prompt,
                metadata=trace_metadata,
            )
        except Exception as exc:  # noqa: BLE001
            log.debug("langfuse_generation_start_failed", error=str(exc))
    try:
        response = httpx.post(
            f"{settings.ollama_base_url.rstrip('/')}/api/generate",
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
        content = str(payload.get("response") or "")
        result = parse_json_response(content)
        if obs:
            try:
                obs.update(
                    output=result,
                    usage_details={
                        "input": payload.get("prompt_eval_count", 0),
                        "output": payload.get("eval_count", 0),
                    },
                )
                obs.end()
            except Exception as trace_exc:  # noqa: BLE001
                log.debug("langfuse_generation_end_failed", error=str(trace_exc))
        return result
    except Exception as exc:  # noqa: BLE001
        log.warning("ollama_generate_failed", task=task, model=model, error=str(exc))
        if obs:
            try:
                obs.update(level="ERROR", status_message=str(exc))
                obs.end()
            except Exception as trace_exc:  # noqa: BLE001
                log.debug("langfuse_generation_error_failed", error=str(trace_exc))
        return None
