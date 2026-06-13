# email-agent

Agente que monitora ~23 contas de e-mail (3 Gmail via API + ~20 IMAP), classifica importância,
detecta spam/documentos/marketing/follow-ups, organiza com labels `AI/*`, aprende com suas ações
e envia resumo matinal via WhatsApp (Evolution API v2). Nunca deleta nada no MVP.

Leia também: [PLANO_REVISADO.md](PLANO_REVISADO.md) — correções do plano original e configuração macOS.

## Stack

Python 3.12 · FastAPI · PostgreSQL · SQLAlchemy/Alembic · Celery + Beat (Redis) · LangGraph ·
scikit-learn · BeautifulSoup4 · IMAPClient · Gmail API · Typer · Label Studio · Ollama (nativo no host).

## Setup inicial

```bash
docker network create ai        # network compartilhada com ../infra (se ainda não existir)
cp .env.example .env            # edite EVOLUTION_*, WHATSAPP_SUMMARY_NUMBER etc.
cp secrets/accounts.example.yml secrets/accounts.yml   # declare suas caixas Gmail/IMAP

docker compose up -d --build    # usa compose.yml (broker: Valkey)
docker compose exec app alembic upgrade head
```

LLM local (no host macOS, não em container — Ollama nativo já instalado):

```bash
# modelo padrão configurado no .env: gemma4:e4b-mlx
```

## Contas

Todas as caixas são declaradas em **`secrets/accounts.yml`** (gmail + imap com host/senha;
ver `secrets/accounts.example.yml`) e sincronizadas com o banco por:

```bash
docker compose exec app email-agent accounts import-yaml
```

Para Gmail, depois do import, autentique cada conta **no host** (abre navegador):

```bash
GMAIL_OAUTH_CLIENT_SECRET_FILE=secrets/gmail_client_secret.json \
GMAIL_TOKEN_STORAGE_PATH=secrets/gmail_tokens \
DATABASE_URL=postgresql+psycopg://emailagent:emailagent@localhost:5433/emailagent \
.venv/bin/email-agent gmail auth voce@gmail.com

docker compose exec app email-agent accounts list
```

> OAuth do Google: app **Desktop**, publishing status **Production** (Testing expira refresh
> token em 7 dias). Detalhes em PLANO_REVISADO.md.

## Regras de importância (agente LLM)

Regras por conta em linguagem natural ficam em `secrets/rules.yml` (ver `rules.example.yml`):
cada regra tem `scope` (conta ou `*`), `description` (instrução pt-BR, com exceções) e `outcome`
(priority/category/labels). O nó `apply_rules` do grafo avalia via Ollama local.

```bash
cp secrets/rules.example.yml secrets/rules.yml   # edite
docker compose exec app email-agent rules import-yaml
docker compose exec app email-agent rules list
docker compose exec app email-agent rules test E-20260612-000134   # debug: avalia 1 mensagem
```

## Operação

```bash
docker compose exec app email-agent run-morning            # SMOKE TEST: sync→classifica→digest (síncrono)
docker compose exec app email-agent run-morning --send     # idem + envia no WhatsApp
docker compose exec app email-agent sync all --bootstrap   # primeira carga (DEFAULT_SYNC_SINCE_DAYS)
docker compose exec app email-agent sync all               # incremental
docker compose exec app email-agent relabel all            # classifica pendentes
docker compose exec app email-agent digest                 # prévia do resumo (1 a 3 mensagens)
docker compose exec app email-agent digest --send          # envia WhatsApp

# correção pontual (menus interativos)
docker compose exec app email-agent feedback E-20260612-000183
docker compose exec app email-agent label E-20260612-000183

# busca de IDs
docker compose exec app email-agent search --label AI/Revisar
docker compose exec app email-agent search --category documento_fiscal --priority P1
docker compose exec app email-agent show E-20260612-000183

# Label Studio (infra compartilhada: cd ../infra/label-studio && docker compose up -d ; UI em localhost:8081)
docker compose exec app email-agent review export-labelstudio --label AI/Revisar --limit 500
docker compose exec app email-agent train import-labelstudio data/exports/anotacoes.json
docker compose exec app email-agent train fit
```

A rotina automática (Celery Beat, timezone `APP_TIMEZONE`): 06:40 sync → 06:50 classifica →
07:00 resumo WhatsApp; 12:30/17:30 incremental; 23:30 manutenção + retreinamento.

## Desenvolvimento e testes

```bash
docker compose exec app pytest        # preferencial: dentro do container
# ou local com uv (host tem Python 3.14; o projeto usa 3.12):
uv venv --python 3.12 .venv && uv pip install -p .venv/bin/python -e ".[dev]"
.venv/bin/pytest
```

Diretrizes para agentes de código (Claude etc.): [CLAUDE.md](CLAUDE.md).
Infra compartilhada (Label Studio, Langfuse, network `ai`, Ollama/embeddings no host):
[../infra/README.md](../infra/README.md).

API local: `http://localhost:8010/health` e `/admin/status` (8010 no host porque a porta 8000 já estava em uso na sua máquina).

## Segurança (MVP)

Nunca deleta/expurga mensagens; spam vira apenas label `AI/Spam Suspeito`; dúvida vira `AI/Revisar`
+ fila `human_review`; toda ação tem `idempotency_key` e trilha em `email_action_log`; corpo de
e-mail só passa por LLM **local** (Ollama); decisões do próprio agente não geram treino.
