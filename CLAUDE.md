# email-agent — diretrizes para agentes

Agente Python que monitora ~23 contas de e-mail (3 Gmail API + ~20 IMAP), classifica, organiza
com labels `AI/*` e envia resumo matinal via WhatsApp (Evolution API **v2**). Plano completo e
correções: [PLANO_REVISADO.md](PLANO_REVISADO.md). Operação: [README.md](README.md).

## Arquitetura (resumo)

- `src/email_agent/` — pacote único; entrypoints: API FastAPI (`api/app.py`), Celery
  (`workers/celery_app.py`, beat embutido), CLI Typer (`cli/app.py`, comando `email-agent`).
- Fluxo: sync (`sync/`) → persistência+dedup (`sync/persist.py`) → pipeline LangGraph
  (`intelligence/graph.py`: regras → modelo sklearn → followup → LLM só se incerto → safety gate)
  → labels via `actions/`.
- Models SQLAlchemy todos em `models/entities.py` (consolidado de propósito — facilita
  autogenerate do Alembic). Migrations em `migrations/`.
- Contas declaradas em `secrets/accounts.yml` (ver `secrets/accounts.example.yml`);
  importadas com `email-agent accounts import-yaml`.
- Regras de importância por conta em `secrets/rules.yml` (descrição pt-BR + outcome),
  importadas com `email-agent rules import-yaml`, avaliadas pelo nó `apply_rules` do grafo
  via LLM (uma chamada Ollama por e-mail quando a conta tem regras). Tabela `email_rule`.

## Regras inegociáveis (política do MVP)

1. **Nunca** implementar delete/expunge/mover para Trash ou Spam do provedor. Ação "negativa"
   máxima = aplicar label `AI/Spam Suspeito`.
2. Dúvida/conflito → `AI/Revisar` + registro em `human_review`. Não decidir automaticamente.
3. Toda ação no provedor passa por `actions/safety_gate.py` com `idempotency_key` e log em
   `email_action_log`. Sem exceções nem atalhos.
4. Decisão automática do próprio agente **não** vira evento de treino confiável
   (`email_training_event.trusted=true` só para: feedback CLI, Label Studio, ações do usuário).
5. Corpo de e-mail não sai da máquina: LLM é **Ollama local** (`host.docker.internal:11434`,
   nativo no host; Docker no macOS não tem GPU). Não adicionar chamadas a LLMs externos.
6. Falha em uma conta não pode interromper o processamento das demais (capturar, logar, seguir).
7. Spam do provedor é **sinal**, não verdade.

## Convenções

- Python 3.12, SQLAlchemy 2.0 estilo `Mapped`/`mapped_column`, Pydantic v2.
- Logging: `from email_agent.logging_setup import get_logger` (structlog JSON). Não usar `print`.
- Strings de usuário/labels/docs em pt-BR; código e identificadores em inglês.
- IDs internos: `E-YYYYMMDD-NNNNNN` (sequence global do Postgres, `ids.py`).
- IMAP: `provider_message_id = "pasta:uidvalidity:uid"`; labels AI viram **cópia** para pasta
  `AI.…` (IMAP não tem labels). Gmail: labels reais via API. Conexão IMAP: SSL implícito 993 por
  padrão; `port`/`starttls`/`ssl` configuráveis por conta em `accounts.yml`.
- Evolution API é **v2**: payload plano `{"number", "text"}`, header `apikey`. Não regredir para
  o formato v1 `textMessage`.

## Como testar

```bash
# Testes unitários (preferencial: dentro do container)
docker compose exec app pytest
# ou local (uv + Python 3.12; o host tem 3.14, não use o python do sistema)
uv venv --python 3.12 .venv && uv pip install -p .venv/bin/python -e ".[dev]" && .venv/bin/pytest

# Stack: subir, migrar, smoke
docker compose up -d --build
docker compose exec app alembic upgrade head
curl -s localhost:8010/health        # porta 8010 no host (8000 está ocupada nesta máquina)
docker compose exec app email-agent accounts list
docker compose exec app email-agent digest          # gera resumo sem enviar
```

Toda mudança em `models/entities.py` exige migration:
`docker compose exec app alembic revision --autogenerate -m "..."` + revisar o arquivo gerado.

Novos comportamentos de classificação devem ganhar teste em `tests/test_classifier.py`
(seguir o padrão `_classify(**kwargs)`). Rodar a suíte inteira antes de concluir.

## Infraestrutura compartilhada

Serviços comuns aos agentes desta máquina vivem em `../infra` (Label Studio, Langfuse) na
network Docker externa **`ai`** — este compose já se conecta a ela. Dentro dos containers,
Label Studio = `http://label-studio:8080`, Langfuse = `http://langfuse-web:3000`.
Ver `../infra/README.md`.
