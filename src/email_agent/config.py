from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+psycopg://emailagent:emailagent@localhost:5433/emailagent"
    redis_url: str = "redis://localhost:6379/0"
    app_timezone: str = "America/Bahia"

    evolution_base_url: str = ""
    evolution_api_key: str = ""
    evolution_instance_name: str = ""
    whatsapp_summary_number: str = ""

    gmail_oauth_client_secret_file: str = "/secrets/gmail_client_secret.json"
    gmail_token_storage_path: str = "/secrets/gmail_tokens"

    ollama_base_url: str = "http://host.docker.internal:11434"
    # Modelo base: classificação/resumo por e-mail (barato, roda muitas vezes).
    ollama_model: str = "qwen3:8b"
    # Modelo para tarefas mais complexas (ex.: priorização de P0). Vazio = usa o base.
    ollama_model_reasoning: str = ""
    llm_enabled: bool = True

    default_sync_since_days: int = 365
    default_bootstrap_mailboxes: str = "inbox,spam,sent"
    max_email_text_chars: int = 12000

    # Digest: e-mails mais antigos que isto não entram no resumo do WhatsApp.
    digest_max_age_days: int = 90
    # Sugestão de limpeza: idade mínima para um e-mail virar candidato a exclusão.
    cleanup_min_age_days: int = 90
    # Auto-archive diário: Importante/Documento já lido com mais de N dias (6 meses)
    # sai da INBOX para AI/Archive.
    archive_auto_min_age_days: int = 180

    spam_model_path: str = "/data/models/spam_model.joblib"
    # Modelo multiclasse de categoria (todas as labels, não só spam/ham).
    category_model_path: str = "/data/models/category_model.joblib"
    training_min_events: int = 20
    # Cascata de decisão: regras determinísticas > ML tradicional > LLM.
    # `category_confidence_threshold` é o CUTOFF do ML: se o modelo de categoria
    # prevê com p >= este valor, a decisão é dele e a LLM é dispensada.
    category_confidence_threshold: float = 0.70
    # Abaixo desta confiança final (nem regra forte, nem ML confiante), o e-mail
    # cai para a LLM (último degrau da cascata) — ver intelligence/graph.py.
    llm_min_confidence: float = 0.60

    label_studio_url: str = ""
    label_studio_api_key: str = ""
    # Projeto no Label Studio: se 0, é resolvido/criado por título (label_studio_project_title).
    label_studio_project_id: int = 0
    label_studio_project_title: str = "email-agent"
    # Classificações com confiança abaixo disto entram na fila de revisão do Label Studio.
    label_studio_low_confidence: float = 0.6
    # Quantos P0/P1 amostrar por sync para auditoria de prioridade (0 = nenhum).
    label_studio_priority_sample: int = 10

    langfuse_base_url: str = ""
    langfuse_public_key: str = ""
    langfuse_secret_key: str = ""

    log_level: str = "INFO"

    @property
    def langfuse_enabled(self) -> bool:
        return bool(self.langfuse_public_key and self.langfuse_secret_key)

    @property
    def label_studio_enabled(self) -> bool:
        return bool(self.label_studio_url and self.label_studio_api_key)

    def model_for(self, task: str = "base") -> str:
        """Modelo Ollama para uma task. 'reasoning' usa o modelo grande (com
        fallback para o base); qualquer outra coisa usa o base."""
        if task == "reasoning":
            return self.ollama_model_reasoning or self.ollama_model
        return self.ollama_model


@lru_cache
def get_settings() -> Settings:
    return Settings()
