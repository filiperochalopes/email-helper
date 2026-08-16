#!/bin/sh
set -eu

LABEL="br.com.email-helper.sync"
INTERVAL_SECONDS=300
SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
PROJECT_DIR=$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)
USER_ID=$(id -u)
LAUNCH_DOMAIN="gui/$USER_ID"
LAUNCH_AGENTS_DIR="$HOME/Library/LaunchAgents"
LOG_DIR="$HOME/Library/Logs"
PLIST_PATH="$LAUNCH_AGENTS_DIR/$LABEL.plist"

uninstall() {
    launchctl bootout "$LAUNCH_DOMAIN" "$PLIST_PATH" >/dev/null 2>&1 || true
    rm -f "$PLIST_PATH"
    printf 'Agendamento removido: %s\n' "$LABEL"
}

if [ "${1:-}" = "--uninstall" ]; then
    uninstall
    exit 0
fi

if [ "$#" -ne 0 ]; then
    printf 'Uso: sh scripts/install-macos-scheduler.sh [--uninstall]\n' >&2
    exit 2
fi

if [ "$(uname -s)" != "Darwin" ]; then
    printf 'Este instalador é exclusivo para macOS.\n' >&2
    exit 1
fi

if [ ! -f "$PROJECT_DIR/compose.yml" ]; then
    printf 'compose.yml não encontrado em %s\n' "$PROJECT_DIR" >&2
    exit 1
fi

DOCKER_PATH=$(command -v docker 2>/dev/null || true)
if [ -z "$DOCKER_PATH" ]; then
    for candidate in /usr/local/bin/docker /opt/homebrew/bin/docker; do
        if [ -x "$candidate" ]; then
            DOCKER_PATH=$candidate
            break
        fi
    done
fi

if [ -z "$DOCKER_PATH" ]; then
    printf 'Docker não encontrado. Instale/inicie o Docker Desktop e tente novamente.\n' >&2
    exit 1
fi

xml_escape() {
    printf '%s' "$1" | sed -e 's/&/\&amp;/g' -e 's/</\&lt;/g' -e 's/>/\&gt;/g'
}

PROJECT_DIR_XML=$(xml_escape "$PROJECT_DIR")
DOCKER_PATH_XML=$(xml_escape "$DOCKER_PATH")
LOG_DIR_XML=$(xml_escape "$LOG_DIR")

mkdir -p "$LAUNCH_AGENTS_DIR" "$LOG_DIR"

TEMP_PLIST=$(mktemp "${TMPDIR:-/tmp}/email-helper-sync.XXXXXX")
trap 'rm -f "$TEMP_PLIST"' EXIT HUP INT TERM

cat >"$TEMP_PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>$LABEL</string>
  <key>ProgramArguments</key>
  <array>
    <string>$DOCKER_PATH_XML</string>
    <string>compose</string>
    <string>exec</string>
    <string>-T</string>
    <string>email-helper-app</string>
    <string>agent</string>
    <string>sync</string>
    <string>all</string>
  </array>
  <key>WorkingDirectory</key>
  <string>$PROJECT_DIR_XML</string>
  <key>RunAtLoad</key>
  <true/>
  <key>StartInterval</key>
  <integer>$INTERVAL_SECONDS</integer>
  <key>StandardOutPath</key>
  <string>$LOG_DIR_XML/email-helper-sync.log</string>
  <key>StandardErrorPath</key>
  <string>$LOG_DIR_XML/email-helper-sync-error.log</string>
</dict>
</plist>
EOF

plutil -lint "$TEMP_PLIST" >/dev/null
launchctl bootout "$LAUNCH_DOMAIN" "$PLIST_PATH" >/dev/null 2>&1 || true
mv "$TEMP_PLIST" "$PLIST_PATH"
trap - EXIT HUP INT TERM
launchctl bootstrap "$LAUNCH_DOMAIN" "$PLIST_PATH"
launchctl kickstart -k "$LAUNCH_DOMAIN/$LABEL"

printf 'Agendamento instalado e iniciado.\n'
printf 'Projeto: %s\n' "$PROJECT_DIR"
printf 'Docker: %s\n' "$DOCKER_PATH"
printf 'Intervalo: %s segundos\n' "$INTERVAL_SECONDS"
printf 'Configuração: %s\n' "$PLIST_PATH"
printf 'Logs: %s/email-helper-sync.log e %s/email-helper-sync-error.log\n' "$LOG_DIR" "$LOG_DIR"
