"""TUI vintage (Rich + readchar) para gerir contas e regras declaradas em YAML.

Roda no host (`.venv/bin/agent tui`). Edita `secrets/accounts.yml` e
`secrets/rules.yml` preservando comentários (ruamel.yaml), dispara os comandos
`import-yaml` (no Docker, se o stack estiver de pé) e o fluxo OAuth do Gmail
(`gmail auth`, sempre no host, pois abre o navegador).
"""
