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

## Console TUI (contas e regras)

Em vez de editar os YAML na mão, há um console interativo estilo DOS (Rich + readchar)
que lê/escreve `secrets/accounts.yml` e `secrets/rules.yml` (preservando comentários),
dispara o `import-yaml` (no container, se o stack estiver de pé) e o OAuth do Gmail
(no host). **Rodar no host**, pois a reautenticação abre o navegador:

```bash
.venv/bin/email-agent tui
```

Navegação por ↑/↓ e Enter; Esc volta. Permite adicionar/editar/remover contas Gmail e
IMAP, reautenticar o Gmail, criar regras (inclusive o atalho "marcar domínio como spam
suspeito" → label `AI/Spam Suspeito`, nunca deleção) e ver o `auth_status` do banco.

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

# Label Studio — sync automático via API (precisa de LABEL_STUDIO_URL + LABEL_STUDIO_API_KEY no .env)
docker compose exec app email-agent review push              # envia pendentes (pré-anotados) ao LS
docker compose exec app email-agent train pull-labelstudio   # puxa anotações concluídas -> eventos de treino
docker compose exec app email-agent train fit                # treina o modelo com o que já temos
docker compose exec app email-agent train stats              # panorama do que está treinado

# Label Studio — modo arquivo (offline, alternativo ao sync via API)
docker compose exec app email-agent review export-labelstudio --label AI/Revisar --limit 500
docker compose exec app email-agent train import-labelstudio data/exports/anotacoes.json
```

> O `review push` + `train pull-labelstudio` rodam **sozinhos** na manutenção noturna (23:30).
> Os dois caminhos (API e arquivo) coexistem; use o de API no dia a dia.

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

## Como o agente decide e aprende

O pipeline ([`intelligence/graph.py`](src/email_agent/intelligence/graph.py)) é em camadas, da
mais barata para a mais cara. **A LLM já é fallback** — só roda em dúvida ou para gerar o resumo:

| Camada | O quê | Quando roda | Custo |
|--------|-------|-------------|-------|
| 1. Regras de código | regex de fiscal/golpe/marketing/urgência, VIP/bloqueio/impersonação de remetente ([`rules.py`](src/email_agent/intelligence/rules.py)) | sempre | grátis |
| 2a. Modelo sklearn spam | `HashingVectorizer + SGDClassifier(log_loss)`, spam vs ham ([`spam_model.py`](src/email_agent/intelligence/spam_model.py)) | sempre que treinado | µs |
| 2b. Modelo sklearn categoria | mesmo algoritmo, **multiclasse** (todas as labels) com `predict_proba` ([`category_model.py`](src/email_agent/intelligence/category_model.py)) | sempre que treinado | µs |
| 2.5 Regras por conta | `rules.yml` avaliadas por conta | só contas com regra | 1 chamada Ollama |
| 3. LLM (Ollama) | resumo legível + desempate ([`summarizer.py`](src/email_agent/intelligence/summarizer.py)) | **só** se `confidence < 0.6` **ou** P0/P1 | 1 chamada Ollama |

**Como as camadas se combinam** ([`classifier.py`](src/email_agent/intelligence/classifier.py)):
`spam_score = 0.5·regras + 0.5·modelo`. Thresholds: `SPAM_THRESHOLD = 0.75`;
banda incerta `0.40–0.75` + sinais de importância ⇒ `AI/Revisar`.

**Grau de confiança do sklearn:** o `SGDClassifier(loss="log_loss")` dá `predict_proba`, então
o modelo de categoria devolve `(categoria, confiança)`. Quando essa confiança
`≥ CATEGORY_CONFIDENCE_THRESHOLD` (0.70) e não há conflito a revisar, **a previsão do modelo é
usada e a confiança final sobe — isso dispensa a LLM**. É o mecanismo que, conforme você treina,
manda cada vez menos decisões para a LLM. A LLM nunca decide o label sozinha: desempata e resume.

**Quanto mais você corrige, menos a LLM é acionada:** o modelo da camada 2 fica mais
confiante (sobe a `confidence`), então menos e-mails caem na faixa incerta que chama a LLM.

### O que o scikit-learn treina

**Dois modelos**, ambos treinados pelo mesmo lote de eventos ([`fit_models`](src/email_agent/intelligence/training.py)):
o **binário spam/ham** (`SpamModel`) e o **multiclasse de categoria** (`CategoryModel`, prevê
todas as labels: fiscal, marketing, promoção, importante, etc.). Aprendem **só com eventos
confiáveis** (`trusted=true`, mínimo `TRAINING_MIN_EVENTS=20` no lote), de três fontes
([`training.py`](src/email_agent/intelligence/training.py)):

**Features dos modelos** ([`features.py`](src/email_agent/intelligence/features.py), idênticas no
treino e na predição): **nome do remetente + domínio** (token `dom_x_y_z`) + assunto + corpo. Incluir
o remetente é o que permite o modelo **aprender fraude/impersonação** por conta própria, não só pelo
texto. Reply-To e datas ainda **não** entram (Reply-To exigiria capturar no parser + migration).

- **Rótulos manuais** — anotação no Label Studio (`source=label_studio`) e feedback CLI
  (`explicit_cli_feedback`). Peso 1.0.
- **Rótulos implícitos** — suas ações na caixa viram treino com peso: `moved_from_spam_to_inbox`
  e `replied` ⇒ ham (0.9); `moved_to_spam`/`added AI/Spam` ⇒ spam (0.9);
  `moved_to_trash` de um marketing ⇒ ignorar (0.6). **Exemplo do "era importante e foi pro spam":**
  isso gera um evento `moved_to_spam` ⇒ amostra de spam, e na próxima vez aquele padrão pesa mais
  para spam. **Reforço positivo:** excluir um `AI/Spam Suspeito` **sem** tirar o label confirma
  que o agente acertou ⇒ amostra de spam com **peso máximo 1.0**.
- Decisão **automática do próprio agente nunca** vira treino (política do MVP).

Veja tudo que já está acumulado com **`email-agent train stats`** (rótulos manuais × implícitos ×
feedback por mudança de status × classificações automáticas × estado do modelo).

### Label Studio (loop de anotação)

Com `LABEL_STUDIO_URL` + `LABEL_STUDIO_API_KEY` no `.env`, o agente
([`labelstudio/sync.py`](src/email_agent/labelstudio/sync.py)):

1. **push** — cria/usa o projeto `email-agent` e envia como tasks **pré-anotadas** (a sugestão
   já vem marcada, você só confirma/corrige) os e-mails de: `AI/Revisar`, `AI/Spam Suspeito`,
   `AI/Lixo Sugerido`, confiança `< LABEL_STUDIO_LOW_CONFIDENCE` e uma amostra de P0/P1
   (`LABEL_STUDIO_PRIORITY_SAMPLE`). Registra em `human_review` para não reenviar.
2. **pull** — lê as tasks anotadas e cria eventos de treino confiáveis, que o `train fit` consome.

Ambos rodam na manutenção das 23:30 (polling). Se as chaves não estiverem setadas, viram no-op.

### Observabilidade (Langfuse)

Toda chamada Ollama vira um trace no Langfuse ([`ollama_client.py`](src/email_agent/intelligence/ollama_client.py)).
O SDK v4 envia em batch num thread de background; por isso o cliente registra `flush()` no
`atexit` — sem ele, processos curtos (CLI, run-morning) terminavam antes do envio e os traces
sumiam. Requer `LANGFUSE_PUBLIC_KEY`/`SECRET_KEY`/`BASE_URL` no `.env`.

### Fraude com remetente falsificado (impersonação)

Além do conteúdo (`SCAM_PATTERNS`), domínio bloqueado e anexo suspeito, há uma checagem de
**impersonação de marca** ([`detect_sender_spoof`](src/email_agent/intelligence/rules.py)): se o
**nome exibido ou o assunto** citam uma marca conhecida (Registro BR, bancos, Correios, Receita,
Apple, Microsoft, PayPal, Mercado Livre…) mas o **domínio de envio não é o oficial**, o e-mail vai
direto para `AI/Spam Suspeito` — mesmo que o conteúdo pareça importante. Caso real que motivou isto:
display *"Registro BR"* enviando de `@stetnet.com.br` com fatura urgente. A lista de marcas é
conservadora e fácil de estender em `IMPERSONATION_BRANDS` (só dispara quando marca **e** domínio
divergem, então o falso-positivo é baixo). Quando dispara, além de `AI/Spam Suspeito` o e-mail
recebe a sub-label **`AI/Spam Suspeito/Fraude`** para você distinguir impersonação de spam comum.
E como o **remetente agora é feature do modelo** (ver acima), o sklearn passa a generalizar fraudes
parecidas mesmo fora da lista, conforme você treina.

**Ainda não coberto:** autenticação real do remetente (SPF/DKIM/DMARC via `Authentication-Results`),
que exige persistir os headers crus (hoje não guardados), e reputação de domínio. É o próximo passo
para fraudes que não citam uma marca da lista.

## Segurança (MVP)

Nunca deleta/expurga mensagens; spam vira apenas label `AI/Spam Suspeito`; dúvida vira `AI/Revisar`
+ fila `human_review`; toda ação tem `idempotency_key` e trilha em `email_action_log`; corpo de
e-mail só passa por LLM **local** (Ollama); decisões do próprio agente não geram treino.
