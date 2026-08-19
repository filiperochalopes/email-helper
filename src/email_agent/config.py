from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+psycopg://emailagent:emailagent@localhost:5433/emailagent"
    evolution_base_url: str = ""
    evolution_api_key: str = ""
    evolution_instance_name: str = ""
    whatsapp_summary_number: str = ""

    gmail_oauth_client_secret_file: str = "/secrets/gmail_client_secret.json"
    gmail_token_storage_path: str = "/secrets/gmail_tokens"

    # `ollama`, `openai_compatible` ou vazio/`disabled` para desligar.
    llm_provider: str = "ollama"
    llm_base_url: str = "http://host.docker.internal:11434"
    llm_api_token: str = ""
    llm_model: str = "qwen3:8b"
    llm_max_concurrency: int = 1
    # Formato pedido ao provider OpenAI-compatible: `json_object` (padrão, OpenAI),
    # `text` ou `off` para omitir o campo. LM Studio recusa `json_object` — só
    # aceita `json_schema` ou `text`. Não afeta Ollama, que usa `format: json`.
    llm_json_mode: str = "json_object"

    default_sync_since_days: int = 365
    max_email_text_chars: int = 12000

    # Digest: e-mails mais antigos que isto não entram no resumo do WhatsApp.
    digest_max_age_days: int = 90
    # Sugestão de limpeza: idade mínima para um e-mail virar candidato a exclusão.
    cleanup_min_age_days: int = 90
    # Abaixo deste valor a triagem da LLM entra na fila humana de revisão.
    llm_min_confidence: float = 0.60

    langfuse_base_url: str = ""
    langfuse_public_key: str = ""
    langfuse_secret_key: str = ""

    log_level: str = "INFO"

    @property
    def langfuse_enabled(self) -> bool:
        return bool(self.langfuse_public_key and self.langfuse_secret_key)

    @property
    def llm_enabled(self) -> bool:
        return self.llm_provider.strip().lower() not in {"", "disabled", "none"}


@lru_cache
def get_settings() -> Settings:
    return Settings()
