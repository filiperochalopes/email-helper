# email-agent — diretrizes para agentes

Agente Python para várias contas Gmail/IMAP, com triagem pelo Ollama local,
revisão conservadora de limpeza e resumo opcional via WhatsApp (Evolution API
**v2**). Produto e roadmap: [docs/REVISAO_E_ROADMAP_FOCO.md](docs/REVISAO_E_ROADMAP_FOCO.md).
Operação: [README.md](README.md).

## Arquitetura (resumo)

- `src/email_agent/` — pacote único; entrypoints: API FastAPI (`api/app.py`) e
  CLI Typer (`cli/app.py`, comando `email-agent`). Não há broker nem workers.
- Fluxo: sync (`sync/`) → persistência+dedup (`sync/persist.py`) → triagem pelo
  Ollama local → regras por conta → safety gate → ações via `actions/`.
- Models SQLAlchemy todos em `models/entities.py` (consolidado de propósito — facilita
  autogenerate do Alembic). Migrations em `migrations/`.
- Contas declaradas em `secrets/accounts.yml` (ver `secrets/accounts.example.yml`);
  importadas com `email-agent accounts import-yaml`.
- Regras de importância por conta em `secrets/rules.yml` (descrição pt-BR + outcome),
  importadas com `email-agent rules import-yaml`, avaliadas pelo nó `apply_rules` do grafo
  via LLM (uma chamada Ollama por e-mail quando a conta tem regras). Tabela `email_rule`.

## Regras inegociáveis (política do MVP)

1. O **pipeline automático** nunca deleta/expunge/move para Trash ou Spam do provedor. Ação
   "negativa" máxima do agente = aplicar label `AI/Spam Suspeito`. Exceção única e explícita:
   o comando CLI `delete`/`rm`, disparado pelo usuário, que mostra o corpo e confirma **um a um**,
   **move para a Lixeira** (recuperável, nunca expunge) via `actions/delete_actions.py` — com
   `idempotency_key` e log em `email_action_log`. `safety_gate.py` permanece sem caminho destrutivo.
2. Dúvida/conflito → registro em `human_review`, sem criar label no provedor. Não decidir
   automaticamente.
3. Toda ação no provedor passa por `actions/safety_gate.py` com `idempotency_key` e log em
   `email_action_log`. Sem exceções nem atalhos.
4. A inferência é sempre local pelo **Ollama** (`host.docker.internal:11434`; Docker no macOS
   não tem GPU). Não adicionar chamadas a LLMs externos. Exceção de observabilidade: se o
   usuário configurar explicitamente as chaves do Langfuse, prompts e respostas podem ser
   enviados à instância externa indicada; sem as chaves, essa exportação permanece desligada.
5. Falha em uma conta não pode interromper o processamento das demais (capturar, logar, seguir).
6. Spam do provedor é **sinal**, não verdade.

## Segredos — não inspecionar

- `secrets/` é local, ignorado pelo Git e necessário para configurar as contas.
- Agentes **não devem abrir, imprimir, pesquisar com `rg`/`grep`, resumir ou copiar**
  `secrets/accounts.yml`, tokens OAuth, senhas, chaves ou qualquer arquivo dentro
  de `secrets/`.
- É permitido executar os conectores/comandos da aplicação que consomem esses
  arquivos, desde que a saída solicitada não exponha credenciais nem conteúdo de
  e-mails. Para diagnosticar contas, prefira IDs, status, nomes de pasta e flags
  IMAP já filtrados pela aplicação.

## Convenções

- Python 3.12, SQLAlchemy 2.0 estilo `Mapped`/`mapped_column`, Pydantic v2.
- Logging: `from email_agent.logging_setup import get_logger` (structlog JSON). Não usar `print`.
- Strings de usuário/labels/docs em pt-BR; código e identificadores em inglês.
- IDs internos: `E-YYYYMMDD-NNNNNN` (sequence global do Postgres, `ids.py`).
- IMAP: `provider_message_id = "pasta:uidvalidity:uid"`; labels AI que organizam mensagens
  viram movimento para uma pasta `AI.…` (IMAP não tem labels), nunca cópia. O arquivamento
  usa a pasta nativa anunciada por `\\Archive`; se não houver, usa/cria `Archive`. Gmail usa
  labels reais e arquiva removendo `INBOX`. Conexão IMAP: SSL implícito 993 por padrão;
  `port`/`starttls`/`ssl` são configuráveis por conta em `accounts.yml`.
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

Novos comportamentos de triagem devem ganhar teste em `tests/test_triage.py`.
Rodar a suíte inteira antes de concluir.

## Infraestrutura compartilhada

Langfuse é opt-in e pode apontar para a instância externa configurada no `.env`.
