# Modelos LLM (Ollama)

O agente usa **Ollama local** (nativo no host macOS; containers acessam via
`host.docker.internal:11434`). O corpo do e-mail nunca sai da máquina.

## Dois modelos, por tarefa

Toda chamada passa por `intelligence/ollama_client.generate_json(prompt, task=...)`, que
escolhe o modelo conforme a tarefa via `settings.model_for(task)`:

| Variável de ambiente      | `task`        | Onde é usado                                   | Sugestão de modelo |
|---------------------------|---------------|------------------------------------------------|--------------------|
| `OLLAMA_MODEL`            | `base`        | Classificação/resumo por e-mail (roda muito):  | `gemma4:e2b-mlx`   |
|                           |               | `summarizer`, `rule_agent`                     |                    |
| `OLLAMA_MODEL_REASONING`  | `reasoning`   | Tarefas complexas que rodam pouco:             | `gemma4:e4b-mlx`   |
|                           |               | `prioritizer` (ranking de P0)                  |                    |

- `OLLAMA_MODEL_REASONING` **vazio** ⇒ cai para o `OLLAMA_MODEL` (sem custo extra de setup).
- `LLM_ENABLED=false` desliga todas as chamadas (úteis em testes/CI).

Exemplo de `.env`:

```env
OLLAMA_BASE_URL=http://host.docker.internal:11434
OLLAMA_MODEL=gemma4:e2b-mlx
OLLAMA_MODEL_REASONING=gemma4:e4b-mlx
LLM_ENABLED=true
```

Baixe os dois no host: `ollama pull gemma4:e2b-mlx && ollama pull gemma4:e4b-mlx`.

## Como adicionar uma nova tarefa com modelo dedicado

1. Em `config.py`, adicione (se quiser um 3º modelo) um campo `ollama_model_<task>` e trate-o
   em `Settings.model_for`.
2. Na sua função, chame `generate_json(prompt, task="<nome>")`.

LangGraph não precisa de configuração especial: cada nó simplesmente lê o modelo da sua tarefa.

## Priorização de P0 (`intelligence/prioritizer.py`)

Quando o digest tem mais de `PRIORITIZE_THRESHOLD` (5) itens P0, uma segunda passada usa o
modelo `reasoning` para reordenar por urgência real: resposta minha obrigatória/iminente >
conversa pessoal (não-empresa) > recência. É **consultivo** — qualquer falha mantém a ordem
por `importance_score`.
