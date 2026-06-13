# Revisão do plano email-agent

Revisão feita em 12/06/2026, com verificação de fontes externas. O plano original é sólido —
arquitetura, taxonomia de labels, política de segurança e ordem de implementação estão corretos.
Abaixo, **apenas o que estava errado, desatualizado ou faltando**, e as decisões tomadas na implementação.

## Correções factuais (verificadas com fontes)

### 1. Evolution API — payload estava no formato v1 (obsoleto) ❌→✅
O plano (seção 23) usa `{"number": ..., "textMessage": {"text": ...}}`. Esse é o formato da **v1**.
Na **v2** o payload é plano:

```json
{ "number": "5571999999999", "text": "resumo...", "linkPreview": false }
```

Auth via header `apikey`. Implementado corretamente em
`src/email_agent/connectors/evolution_client.py`.
Fonte: https://doc.evolution-api.com/v2/api-reference/message-controller/send-text

### 2. Gmail OAuth — modo Testing expira refresh token em 7 dias ✅ (plano já intuía, agora confirmado)
- App em **Testing** ⇒ refresh tokens expiram em **7 dias**. Inaceitável para a rotina.
- Solução: publicar em **Production** no Google Cloud Console, **sem verificação**. O escopo
  `gmail.modify` é restrito, então aparece a tela "Google hasn't verified this app" — para as suas
  3 contas próprias basta clicar em *Advanced → Go to ... (unsafe)* uma vez por conta. Limite de
  ~100 usuários não verificados não afeta uso pessoal.
- Passo a passo: criar projeto → ativar Gmail API → OAuth consent screen (External, Production) →
  credencial tipo **Desktop app** → baixar JSON para `secrets/gmail_client_secret.json`.
- Fontes: https://developers.google.com/identity/protocols/oauth2 ("Refresh token expiration"),
  https://support.google.com/cloud/answer/15549945

### 3. Gmail history API — historyId expira (404)
Confirmado: `users.history.list` com `startHistoryId` antigo retorna **404** e a documentação diz
que o id vale "tipicamente uma semana, às vezes só horas". O sync implementado
(`sync/gmail_sync.py`) trata 404 com fallback para busca por janela de dias. O plano não previa isso
explicitamente — agora está coberto.

### 4. Ollama: rodar NATIVO no macOS, nunca em container
O plano não dizia onde o Ollama roda. Decisão (confirmada por fontes): Docker no macOS **não tem
acesso à GPU Apple Silicon/Metal** — Ollama em container roda só em CPU (3–5x mais lento).
Portanto: **Ollama nativo no host** (como já está instalado), containers acessam via
`http://host.docker.internal:11434`. No Docker Desktop/OrbStack isso funciona mesmo com o bind
padrão do Ollama em 127.0.0.1 (o proxy do Docker origina a conexão do próprio host). Se falhar
(ex.: colima ou VPN), exportar `OLLAMA_HOST=0.0.0.0` no host.
Fonte: https://chariotsolutions.com/blog/post/apple-silicon-gpus-docker-and-ollama-pick-two/

## Ajustes de design feitos na implementação

1. **IMAP não tem labels.** "Aplicar label AI" em conta IMAP = **copiar** a mensagem para a pasta
   `AI.Importante` etc. (delimitador detectado por servidor), deixando o original intocado —
   não destrutivo, alinhado à política do MVP. No Gmail são labels de verdade.
2. **`logging.py` renomeado para `logging_setup.py`** — evita confusão com o módulo stdlib.
3. **`provider_message_id` do IMAP** codifica `pasta:uidvalidity:uid`, pois UID só é único por
   pasta+uidvalidity. Dedup entre pastas usa o header `Message-ID` (com fallback para
   from+subject+date+hash do corpo, em `parsing/mime_parser.py:dedupe_fingerprint`).
4. **Sequência global para `E-YYYYMMDD-NNNNNN`** (sequence do PostgreSQL): o contador não reinicia
   por dia — a data no ID é só legibilidade. Reiniciar por dia exigiria contador por data e não
   agrega nada.
5. **Models consolidados** em `models/entities.py` (em vez de 10 arquivos) para facilitar o
   autogenerate do Alembic; o pacote `email_agent.models` reexporta tudo, então os imports ficam
   iguais ao plano.
6. **LangGraph com nós consolidados**: `load_email → classify_message (regras+modelo) →
   detect_followup → llm_node (só incerteza/digest) → safety_gate → persist_result`. Mesma
   semântica dos 12 nós do plano, menos cerimônia.
7. **`ai_labels` no banco refletem a intenção** mesmo se a aplicação no provedor falhar (a falha
   fica registrada em `email_action_log` com status `error` e será reaplicada por idempotência na
   próxima classificação — a chave só bloqueia reaplicação quando status=`success`).
8. **Digest dos jobs de 12:30/17:30**: classificam e registram P0/P1 mas não enviam WhatsApp
   (conforme plano — só o matinal envia).
9. **Versões verificadas no PyPI (jun/2026)**: celery 5.6, redis 8, langgraph 1.2, imapclient 3.1,
   google-api-python-client 2.197 — todos compatíveis com Python 3.12. O `pyproject.toml` usa
   limites mínimos, não pins.

## O que ficou de fora do MVP (consciente)

- Extração de texto de PDF (`email_attachment.extracted_text` existe, extração fica para fase 2).
- Webhook da Evolution API (rota prevista, sem feedback por WhatsApp no MVP).
- Regras dinâmicas do banco (`email_rule` existe; o motor inicial usa regras em código —
  `intelligence/rules.py`).
- Label Studio no compose está sob o profile `labelstudio` (opcional):
  `docker compose --profile labelstudio up -d`.

## Configuração macOS para manter a rotina (06:40 / 12:30 / 17:30 / 23:30)

O agendamento mora no **Celery Beat dentro do Docker** — não precisa de launchd/cron para os jobs.
O que o macOS precisa garantir é: (a) o runtime Docker sobe no login; (b) o Mac está acordado nos
horários.

1. **Runtime**: você já tem Docker 29. Se quiser algo mais leve, **OrbStack** é drop-in
   (mesmo CLI, ~10x menos RAM ociosa que Docker Desktop). Em ambos, ative **Start at login**
   (Settings → General). Os serviços já têm `restart: unless-stopped`, então voltam sozinhos.
2. **Sleep** — escolha um:
   - Mac na tomada / desktop: `sudo pmset -c sleep 0` (nunca dorme em AC; pode desligar a tela
     normalmente). É o mais simples e confiável.
   - Economizar energia: deixe dormir e agende wake diário antes da rotina da manhã:
     `sudo pmset repeat wakeorpoweron MTWRFSU 06:35:00`. Atenção: `pmset repeat` aceita **um único
     horário/dia**, então os jobs de 12:30/17:30/23:30 exigiriam o Mac acordado por outros meios —
     na prática, prefira a opção anterior. Notebook de tampa fechada **sem energia** não acorda
     por agenda; mantenha na tomada.
   - Se o Mac dormir e perder um horário, o Beat não "recupera" o job perdido — rode manualmente
     `docker compose exec worker email-agent sync all` ao acordar, ou use a opção 1.
3. **Ollama**: já roda como app de menu bar com login item próprio (instalação padrão do macOS).
   Confirme em System Settings → Login Items. Modelo: `ollama pull qwen3:8b` (ou ajuste
   `OLLAMA_MODEL` no `.env`).
4. **Evolution API**: se ela rodar em outro host/VPS, aponte `EVOLUTION_BASE_URL` direto; se rodar
   no próprio Mac em Docker, use o nome do serviço na mesma rede ou `host.docker.internal:8080`.
