# Providers e modelos de LLM

Todas as chamadas estruturadas passam por
`intelligence/llm_client.generate_json()`. O adapter devolve `LLMCallResult`,
independentemente do provider:

- `data`: JSON validado pelo parser;
- `provider` e `model`;
- resposta textual bruta;
- tokens de entrada/saída, quando o provider informa;
- latência e erro.

## Configuração única

```dotenv
LLM_PROVIDER=ollama
LLM_BASE_URL=http://host.docker.internal:11434
LLM_API_TOKEN=
LLM_MODEL=gemma4:e2b-mlx
LLM_MAX_CONCURRENCY=6
```

Valores aceitos para `LLM_PROVIDER`:

- `ollama`: usa `POST /api/generate`, JSON mode e Ollama no host ou rede;
- `openai_compatible` ou `openai`: usa `POST /v1/chat/completions` com
  `response_format=json_object`;
- `disabled`, `none` ou vazio: não chama LLM e envia a mensagem para Revisar.

O mesmo modelo atende triagem, regras e priorização. Isso reduz configuração e
torna custo/latência mensuráveis antes de reintroduzir roteamento por tarefa.
As classificações usam um pool local limitado por `LLM_MAX_CONCURRENCY`. Cada
tarefa abre sua própria sessão de banco; o valor mínimo efetivo é 1.

## Persistência

A leitura principal de cada e-mail é armazenada em `email_classification`:

- categoria, prioridade, confiança, resumo e motivo;
- `cleanup_action` (`none`, `archive` ou `trash`) e justificativa;
- `llm_provider`, `llm_model` e `llm_prompt_version`;
- `llm_raw_result` e `llm_raw_response`;
- `llm_input_tokens`, `llm_output_tokens`, `llm_latency_ms` e `llm_error`.

O resultado local não depende do Langfuse. Langfuse complementa a auditoria com
traces e é opt-in; quando ativo, recebe prompts e respostas.

## Privacidade

Ollama local mantém a inferência na máquina. Um provider
`openai_compatible` externo recebe o trecho de corpo e o histórico recente da
thread incluídos no prompt.
`MAX_EMAIL_TEXT_CHARS` limita esse trecho, mas não o anonimiza.
