# email-agent

Assistente local para várias contas Gmail e IMAP. Sincroniza mensagens,
classifica com Ollama, sugere uma fila conservadora de limpeza e mantém toda ação
no provedor rastreável. A LLM nunca apaga e-mail; exclusão continua sendo uma
decisão humana e recuperável via Lixeira.

Roadmap e decisões de produto: [docs/REVISAO_E_ROADMAP_FOCO.md](docs/REVISAO_E_ROADMAP_FOCO.md).

## Stack atual

Python 3.12 · FastAPI · PostgreSQL · SQLAlchemy/Alembic · Ollama local · Gmail
API · IMAPClient · Typer/Rich · Langfuse opt-in.

Não há Celery, Valkey, workers, sklearn, treinamento noturno ou Label Studio.
O fluxo é síncrono e uma falha em uma conta não interrompe as demais.

## Configuração

```bash
cp .env.example .env
cp secrets/accounts.example.yml secrets/accounts.yml
docker compose up -d --build
docker compose exec app alembic upgrade head
docker compose exec app email-agent accounts import-yaml
```

`secrets/accounts.yml` é local, necessário para as credenciais IMAP e ignorado
pelo Git. Não publique esse arquivo. Tokens Gmail ficam em
`secrets/gmail_tokens/`.

Para autorizar uma conta Gmail, rode no host:

```bash
GMAIL_OAUTH_CLIENT_SECRET_FILE=secrets/gmail_client_secret.json \
GMAIL_TOKEN_STORAGE_PATH=secrets/gmail_tokens \
DATABASE_URL=postgresql+psycopg://emailagent:emailagent@localhost:5433/emailagent \
.venv/bin/email-agent gmail auth voce@gmail.com
```

## Operação

```bash
# Fluxo completo e síncrono: sync → triagem Ollama → digest
docker compose exec app email-agent run
docker compose exec app email-agent run --send

# Operações isoladas
docker compose exec app email-agent sync all
docker compose exec app email-agent sync all --bootstrap
docker compose exec app email-agent relabel all
docker compose exec app email-agent digest
docker compose exec app email-agent show E-YYYYMMDD-NNNNNN
docker compose exec app email-agent search --category marketing
```

O agendamento é externo ao aplicativo. Durante desenvolvimento, rode `run`
manualmente. A configuração de `launchd` entra quando os horários definitivos
forem validados.

## Triagem e limpeza

Cada mensagem recebe uma única análise JSON do Ollama com categoria, prioridade,
resumo, confiança, necessidade de ação e `cleanup_candidate`. A pré-seleção de
limpeza é intencionalmente pouco sensível: só marketing, promoção, spam claro,
aviso sem valor futuro ou follow-up sem ação podem ser sugeridos. Documento,
cobrança, segurança, conversa pessoal, prazo, anexo relevante e qualquer dúvida
nunca são pré-selecionados.

`cleanup_candidate` é dado local para a futura tela de revisão em lote. A
triagem não cria label, não move a mensagem e não chama a Lixeira. Os únicos
labels opcionais no provedor são `AI/Foco` e `AI/Spam Suspeito`.

## Arquivo

- Gmail: arquivar remove somente `INBOX`.
- IMAP: usa primeiro a pasta anunciada com `SPECIAL-USE \\Archive`; depois
  reconhece `Archive`, `Archives` e `Arquivados`; se nenhuma existir, cria
  `Archive` e a assina.
- A descoberta evita duplicar a pasta `Archives` criada/reconhecida pelo Canary.

## Langfuse

É opt-in. Configure no `.env` para enviar traces à sua instância:

```dotenv
LANGFUSE_BASE_URL=https://seu-langfuse.example.com
LANGFUSE_PUBLIC_KEY=
LANGFUSE_SECRET_KEY=
```

Sem as duas chaves, o cliente não é inicializado. O Ollama permanece local; ao
ativar Langfuse, prompts e respostas observados passam a ser enviados à
instância configurada.

## Segurança

- O pipeline automático nunca apaga, expurga ou move para Trash/Spam.
- O comando `delete` é explícito, mostra cada mensagem e move para a Lixeira,
  sem expunge.
- Toda ação automática permitida passa pelo safety gate com idempotência e log.
- Falha ou baixa confiança da LLM vira revisão humana e nunca pré-seleção de
  limpeza.

## Testes

```bash
docker compose exec app pytest
# ou
.venv/bin/pytest
```
