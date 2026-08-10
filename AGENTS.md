# email-agent — diretrizes para agentes

Agente Python para várias contas Gmail/IMAP, com triagem por adapter de LLM,
revisão conservadora de limpeza e resumo opcional via WhatsApp (Evolution API
**v2**). Produto e roadmap: [docs/REVISAO_E_ROADMAP_FOCO.md](docs/REVISAO_E_ROADMAP_FOCO.md).
Operação: [README.md](README.md).

## Arquitetura (resumo)

- `src/email_agent/` — pacote único; entrypoints: API FastAPI (`api/app.py`) e
  CLI Typer (`cli/app.py`, comando `email-agent`). A web local usa HTML/JS e
  Tailwind compilado em `web/`; Node é somente ferramenta de build. Não há broker
  nem workers.
- Fluxo: sync (`sync/`) → persistência+dedup (`sync/persist.py`) → triagem pela
  LLM configurada → regras por conta → safety gate → ações via `actions/`.
- Models SQLAlchemy todos em `models/entities.py` (consolidado de propósito — facilita
  autogenerate do Alembic). Migrations em `migrations/`.
- Contas declaradas em `secrets/accounts.yml` (ver `secrets/accounts.example.yml`);
  importadas com `email-agent accounts import-yaml`.
- Regras de importância por conta em `secrets/rules.yml` (descrição pt-BR + outcome),
  importadas com `email-agent rules import-yaml`, avaliadas pelo nó `apply_rules` do grafo
  via LLM (uma chamada adicional por e-mail quando a conta tem regras). Tabela `email_rule`.

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
4. Toda inferência passa por `intelligence/llm_client.py`; não importar SDK de provider em
   outros módulos. Providers permitidos: `ollama` e `openai_compatible`. Ollama é a opção
   local padrão; provider externo envia o trecho do e-mail presente no prompt. Langfuse é
   uma exportação independente e só liga quando ambas as chaves estão configuradas.
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
- LLM: `LLM_PROVIDER`, `LLM_BASE_URL`, `LLM_API_TOKEN` e `LLM_MODEL` são o único contrato
  de configuração. `generate_json()` retorna `LLMCallResult`; chamadas devem tratar erro
  conservadoramente. A triagem persiste provider, modelo, versão do prompt, JSON bruto,
  latência, tokens e erro em `email_classification`. Detalhes em
  [docs/MODELOS.md](docs/MODELOS.md).
- Busca: `email_message.search_vector` é gerado pelo PostgreSQL com configuração `simple`
  para conteúdo multilíngue e indexado com GIN. Fuzzy match usa `pg_trgm`. Novas buscas
  reutilizam `search.email_search_statement`; não voltar a `ILIKE '%...%'` isolado para a
  busca geral. Busca semântica só entra com pipeline real de embeddings e migration pgvector.

## Como testar

```bash
# Testes unitários (preferencial: dentro do container)
docker compose exec email-triage-app pytest
# ou local (uv + Python 3.12; o host tem 3.14, não use o python do sistema)
uv venv --python 3.12 .venv && uv pip install -p .venv/bin/python -e ".[dev]" && .venv/bin/pytest

# Stack: subir, migrar, smoke
docker compose up -d --build
docker compose exec email-triage-app alembic upgrade head
curl -s localhost:8010/health        # porta 8010 no host (8000 está ocupada nesta máquina)
docker compose exec email-triage-app email-agent accounts list
docker compose exec email-triage-app email-agent digest          # gera resumo sem enviar
```

Toda mudança em `models/entities.py` exige migration:
`docker compose exec email-triage-app alembic revision --autogenerate -m "..."` + revisar o arquivo gerado.

Novos comportamentos de triagem devem ganhar teste em `tests/test_triage.py`.
Endpoints e ações da web devem ganhar teste em `tests/test_api_cleanup.py`.
Ao alterar classes Tailwind em `web/`, rodar `npm run build:css` e versionar o CSS.
Rodar a suíte inteira antes de concluir.

## Infraestrutura compartilhada

Langfuse é opt-in e pode apontar para a instância externa configurada no `.env`.
