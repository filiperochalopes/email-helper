# email-helper

Assistente para várias contas Gmail e IMAP. Sincroniza mensagens, faz uma
leitura estruturada por LLM, oferece busca local rápida e monta uma fila
conservadora de limpeza. A IA nunca apaga e-mail; Lixeira e Arquivo exigem ação
explícita do usuário.

Roadmap e decisões de produto: [docs/REVISAO_E_ROADMAP_FOCO.md](docs/REVISAO_E_ROADMAP_FOCO.md).

## Stack atual

Python 3.12 · FastAPI · PostgreSQL 16 · SQLAlchemy/Alembic · Gmail API · IMAP ·
Typer/Rich · adapter HTTP de LLM · Langfuse opt-in.

Não há Celery, Valkey, workers, LangGraph, LangChain, sklearn, treinamento
noturno ou Label Studio. O runtime normal tem apenas `email-helper-app` e
`email-helper-db`; uma
falha em uma conta não interrompe as demais.

## Configuração

```bash
cp .env.example .env
cp secrets/accounts.example.yml secrets/accounts.yml
docker compose up -d --build
docker compose exec email-helper-app alembic upgrade head
docker compose exec email-helper-app agent accounts import-yaml
```

`secrets/accounts.yml` é obrigatório, local e ignorado pelo Git. Tokens Gmail
ficam em `secrets/gmail_tokens/`. Não publique nem copie esses arquivos.

## Provider de LLM

O restante da aplicação não conhece SDKs de providers. Todas as chamadas passam
por `intelligence/llm_client.py` e retornam o mesmo contrato estruturado.

Ollama local:

```dotenv
LLM_PROVIDER=ollama
LLM_BASE_URL=http://host.docker.internal:11434
LLM_API_TOKEN=
LLM_MODEL=gemma4:e2b-mlx
LLM_MAX_CONCURRENCY=6
```

API compatível com OpenAI:

```dotenv
LLM_PROVIDER=openai_compatible
LLM_BASE_URL=https://api.openai.com
LLM_API_TOKEN=troque-aqui
LLM_MODEL=gpt-5-mini
LLM_MAX_CONCURRENCY=6
```

Use `LLM_PROVIDER=disabled` para rodar sem inferência. Nesse caso, mensagens
novas falham de forma conservadora para Revisar. Providers externos recebem o
trecho do e-mail usado no prompt; Ollama local não envia esse conteúdo por si.
`LLM_MAX_CONCURRENCY` limita quantas mensagens são classificadas simultaneamente;
use um valor compatível com a memória disponível para o modelo.

Cada leitura principal é salva em `email_classification`, incluindo resultado
JSON normalizado e bruto, provider, modelo, versão do prompt, latência, tokens e
erro. Isso permite auditoria e reprocessamento sem depender do Langfuse.

## Web local

A fila de limpeza está disponível em [http://127.0.0.1:8010](http://127.0.0.1:8010).
Ela oferece busca, filtros por conta/categoria, carregamento progressivo, modo
`Sugestões da IA` e modo `Todos da Inbox`. Candidatos conservadores chegam
pré-selecionados; o usuário pode corrigir a seleção antes de Arquivar ou mover
até 200 mensagens por vez para a Lixeira recuperável.

Para iniciar com uma base vazia, importe as contas e sincronize as pastas
declaradas na configuração local:

```bash
docker compose exec email-helper-app agent accounts import-yaml
docker compose exec email-helper-app agent sync all --bootstrap
```

Para validar a interface e a observabilidade com uma amostra pequena de uma
conta, use `agent sync once --account EMAIL --limit 20`. O cursor só avança até
as mensagens realmente coletadas, portanto o restante não é perdido.

O bootstrap usa `DEFAULT_SYNC_SINCE_DAYS` como janela histórica, sincroniza as
contas de forma independente e classifica cada mensagem nova. Ele não arquiva
nem envia nada para a Lixeira.

Tailwind é compilado em desenvolvimento e o CSS gerado é servido pelo FastAPI;
Node não faz parte do runtime:

```bash
npm install
npm run build:css
```

## PostgreSQL e busca

A busca usa duas estratégias complementares, sem outro serviço:

- full-text multilíngue com coluna `tsvector` gerada, pesos maiores para assunto
  e remetente, e índice GIN;
- fuzzy match com `pg_trgm` e índices GIN em assunto, nome e e-mail do remetente.

```bash
docker compose exec email-helper-app agent search -q "contrato renovação"
docker compose exec email-helper-app agent search -q "nome com erro de digitacao"
docker compose exec email-helper-app agent search --from fornecedor --category documento
```

A migration também troca índices redundantes por índices alinhados às consultas:
deduplicação por conta, thread+data, Message-ID por conta, digest por data,
classificação única por mensagem, fila parcial de limpeza e revisão pendente.

Busca semântica vetorial ainda não está habilitada. Ela exige `pgvector`, um
modelo específico de embeddings e uma dimensão estável. Esses componentes não
são usados atualmente; adicioná-los agora aumentaria stack e armazenamento sem
produzir resultados. Full-text + trigram cobre busca por conteúdo e tolerância a
typos. Embeddings entram quando houver uma consulta semântica concreta para
medir recall e latência.

## Operação

```bash
# sync → leitura LLM → digest
docker compose exec email-helper-app agent run
docker compose exec email-helper-app agent run --send

docker compose exec email-helper-app agent sync all
docker compose exec email-helper-app agent sync all --bootstrap
docker compose exec email-helper-app agent relabel all
docker compose exec email-helper-app agent digest
docker compose exec email-helper-app agent show E-YYYYMMDD-NNNNNN
```

O agendamento permanece externo ao aplicativo. Durante desenvolvimento, execute
`run` manualmente. Em uma instalação contínua, agende `agent sync all`: esse
comando sincroniza todas as contas ativas e classifica cada mensagem nova, mas
não gera nem envia o digest. O Docker Desktop/Engine e a stack precisam estar
ativos; `docker compose up -d` não instala o agendamento.

### Agendamento a cada 5 minutos

A opção `-T` evita que o agendador tente abrir um terminal interativo. No macOS,
o instalador abaixo resolve todos os caminhos automaticamente. Nos exemplos
manuais de Linux e Windows, substitua os caminhos indicados; no Linux, descubra
o executável do Docker com `command -v docker`.

#### macOS (`launchd`)

Com o Docker Desktop iniciado, execute na raiz do projeto:

```bash
sh scripts/install-macos-scheduler.sh
```

O instalador detecta automaticamente os caminhos absolutos do projeto, do
Docker e do usuário. Em seguida, cria e valida
`~/Library/LaunchAgents/br.com.email-helper.sync.plist`, ativa o `LaunchAgent` e
dispara o primeiro sync. A saída fica em
`~/Library/Logs/email-helper-sync.log` e os erros em
`~/Library/Logs/email-helper-sync-error.log`.

Para desinstalar:

```bash
sh scripts/install-macos-scheduler.sh --uninstall
```

#### Linux (`systemd` do usuário)

Crie `~/.config/systemd/user/email-helper-sync.service`:

```ini
[Unit]
Description=Sincroniza e classifica e-mails do email-helper

[Service]
Type=oneshot
WorkingDirectory=/CAMINHO/ABSOLUTO/email-helper
ExecStart=/CAMINHO/DO/docker compose exec -T email-helper-app agent sync all
```

Crie `~/.config/systemd/user/email-helper-sync.timer`:

```ini
[Unit]
Description=Executa o sync do email-helper a cada 5 minutos

[Timer]
OnBootSec=1min
OnUnitActiveSec=5min
Persistent=true

[Install]
WantedBy=timers.target
```

Ative e confira o timer:

```bash
systemctl --user daemon-reload
systemctl --user enable --now email-helper-sync.timer
systemctl --user list-timers email-helper-sync.timer
journalctl --user -u email-helper-sync.service
```

#### Windows (Agendador de Tarefas)

Em um PowerShell, substitua o caminho do projeto e crie a tarefa:

```powershell
schtasks.exe /Create /F /TN "email-helper sync" /SC MINUTE /MO 5 /TR 'cmd.exe /d /c "cd /d C:\CAMINHO\email-helper && docker compose exec -T email-helper-app agent sync all"'
schtasks.exe /Run /TN "email-helper sync"
```

Para remover a tarefa:

```powershell
schtasks.exe /Delete /F /TN "email-helper sync"
```

Nos três sistemas, valide primeiro o comando manualmente. Se a execução ocorrer
enquanto o computador estiver suspenso ou o Docker estiver parado, ela falhará
sem alterar mensagens no provedor e o ciclo seguinte tentará novamente.

## Triagem e limpeza

Cada mensagem recebe categoria, prioridade, resumo, confiança, necessidade de
ação e `cleanup_action` (`none`, `archive` ou `trash`). A triagem recebe a data
atual, a data da mensagem e, quando disponível, o histórico cronológico da thread.
Marketing puro, promoção expirada e spam claro podem ser sugeridos para a Lixeira;
documentos/evidências e conversas resolvidas podem ser sugeridos para arquivamento.
P0/P1, retorno pendente e dúvida permanecem com `none`.

`cleanup_action` vive somente no PostgreSQL. A triagem não cria label, não
move mensagem e não chama a Lixeira. Os únicos labels opcionais no provedor são
`AI/Foco` e `AI/Spam Suspeito`.

Na web, os modos **Arquivar**, **Lixeira** e **Inbox** separam os destinos.
Ordenação por prioridade mostra **Revisão** antes de P0; revisão é uma incerteza
de classificação, não uma prioridade nem um destino de limpeza.
Após uma mudança de prompt, classificações existentes podem ser atualizadas de
forma explícita com `agent relabel all --force`; sem `--force`, somente pendentes
são processadas.
O checkbox ao lado de “Caixa de entrada” seleciona as mensagens carregadas e
fica parcialmente marcado quando só parte delas está selecionada. A seleção
também aceita Shift-clique; nenhuma mensagem é movida sem a confirmação final.

## Arquivo

- Gmail: remove somente `INBOX`.
- IMAP: prioriza `SPECIAL-USE \\Archive`, reconhece `Archive`, `Archives` e
  `Arquivados`, e cria `Archive` somente quando necessário.

## Langfuse

É opt-in. Sem `LANGFUSE_PUBLIC_KEY` e `LANGFUSE_SECRET_KEY`, o cliente não é
inicializado. Ao ativá-lo, prompts e respostas são enviados à instância
configurada, independentemente de o provider de inferência ser local.

O projeto usa o SDK Python v4 e exige Langfuse self-hosted `>= 3.63.0` para
tracing. O cliente recebe `public_key`, `secret_key` e `base_url` explicitamente
e faz `flush()` ao encerrar comandos curtos. Instâncias anteriores precisam ser
atualizadas; não há fallback para o protocolo de ingestão antigo.

## Segurança

- O pipeline automático nunca apaga, expurga ou move para Trash/Spam.
- `delete` move para a Lixeira recuperável e exige decisão humana.
- Ações no provedor têm idempotência e log.
- Falha ou baixa confiança vira revisão, categoria `revisar` e nunca pré-seleção de limpeza.

## Verificação

```bash
docker compose exec email-helper-app alembic current
docker compose exec email-helper-app ruff check src tests migrations
docker compose exec email-helper-app pytest
curl -fsSL http://127.0.0.1:8010/health
```
