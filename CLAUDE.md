# email-agent — diretrizes para agentes

Agente Python que monitora ~23 contas de e-mail (3 Gmail API + ~20 IMAP), classifica, organiza
com labels `AI/*` e envia resumo matinal via WhatsApp (Evolution API **v2**). Plano completo e
correções: [PLANO_REVISADO.md](PLANO_REVISADO.md). Operação: [README.md](README.md).

## Arquitetura (resumo)

- `src/email_agent/` — pacote único; entrypoints: API FastAPI (`api/app.py`), Celery
  (`workers/celery_app.py`, beat embutido), CLI Typer (`cli/app.py`, comando `email-agent`).
- `tui/` — console interativo estilo DOS (Rich + readchar), comando `email-agent tui` (host).
  Edita `accounts.yml`/`rules.yml` via ruamel (preserva comentários; YAML = fonte de verdade) e
  dispara `import-yaml`/`gmail auth` (`tui/runner.py` escolhe `docker compose exec` vs local).
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

1. O **pipeline automático** nunca deleta/expunge/move para Trash ou Spam **do provedor**. Pode
   **organizar**: aplicar label AI move o e-mail para fora da INBOX (Gmail: adiciona label +
   remove `INBOX`; IMAP: move para a pasta `AI.…`), EXCETO `AI/Importante` e
   `AI/Importante/Aguardando Resposta`, que **ficam na INBOX** (`taxonomy.INBOX_KEEP_LABELS`).
   Nunca há duplicação (não copia mais). Exceções destrutivas, só por comando do usuário:
   `delete`/`rm` (move para a **Lixeira** do provedor) via `actions/delete_actions.py`. O
   arquivamento (`archive`/`archive-one` e o ciclo diário `auto_archive_old`) move para
   `AI/Archive` — **fora da INBOX, recuperável, nunca deleta**. Tudo com `idempotency_key` e
   log em `email_action_log`. `safety_gate.py` permanece sem Trash/Spam/expunge.
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
- IMAP: `provider_message_id = "pasta:uidvalidity:uid"`; aplicar label AI **move** a mensagem para
  a pasta `AI.…` (IMAP não tem labels) — um e-mail vive em UMA pasta só, então quando há várias
  labels que saem da INBOX a pasta destino é decidida por `taxonomy.imap_destination` /
  `IMAP_DEST_PRIORITY`; as demais labels ficam só em `email_message.ai_labels`. Labels que ficam
  na INBOX (Importante/Aguardando) não geram pasta no IMAP (a label vive no banco/digest). Gmail:
  labels reais via API + remoção de `INBOX` para "arquivar". Conexão IMAP: SSL implícito 993 por
  padrão; `port`/`starttls`/`ssl` configuráveis por conta em `accounts.yml`.
- `AI/Archive` = arquivo morto (fora da INBOX, recuperável). Fluxo manual: `email-agent archive
  --before YYYY-MM-DD` (cutoff; ignora marketing/notícia e spam) ou `archive-one E-…`. Ciclo
  automático diário **estrito** (`auto_archive_old`, beat `auto-archive-night`): só
  Importante/Documentos/Fiscal **já lidos** com mais de `archive_auto_min_age_days` (180d).
- Sync IMAP de spam/trash é só para dedup/sinal: essas mensagens **não** são classificadas
  (não recebem label AI). Ver `sync/imap_sync.py` e o guard em `workers/tasks_classify.py`.
- Evolution API é **v2**: payload plano `{"number", "text"}`, header `apikey`. Não regredir para
  o formato v1 `textMessage`.
- LLM: toda chamada ao Ollama passa por `intelligence/ollama_client.generate_json(prompt, task=...)`.
  Dois modelos por env var: `OLLAMA_MODEL` (base, classificação/resumo por e-mail) e
  `OLLAMA_MODEL_REASONING` (tarefas complexas, ex.: priorização de P0; vazio = usa o base).
  Selecione via `settings.model_for("base"|"reasoning")`. Detalhes em [docs/MODELOS.md](docs/MODELOS.md).

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
