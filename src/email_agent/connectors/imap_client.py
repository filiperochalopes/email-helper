"""Conexão IMAP SSL e descoberta de pastas especiais.

Credenciais IMAP ficam declaradas em secrets/accounts.yml (ver accounts.example.yml).
"""
import re

from imapclient import IMAPClient

from email_agent.connectors.accounts_config import imap_credentials
from email_agent.models import EmailAccount

SPAM_FOLDER_RE = re.compile(r"spam|junk|lixo eletr", re.IGNORECASE)
SENT_FOLDER_RE = re.compile(r"sent|enviad", re.IGNORECASE)
TRASH_FOLDER_RE = re.compile(r"trash|deleted|lixeira", re.IGNORECASE)
ARCHIVE_FOLDER_RE = re.compile(r"^archives?$|arquivad[oa]s?", re.IGNORECASE)


def connect(account: EmailAccount) -> IMAPClient:
    """Conecta conforme declarado em accounts.yml.

    Padrão: SSL implícito na 993 (recomendado). Alternativas por conta:
    `port: 143` + `starttls: true` (TLS explícito) ou `ssl: false` (sem TLS;
    só para servidor local/teste).
    """
    creds = imap_credentials(account.email_address)
    use_ssl = creds.get("ssl", True) and not creds.get("starttls", False)
    client = IMAPClient(
        creds.get("host", account.imap_host),
        port=creds.get("port", account.imap_port or (993 if use_ssl else 143)),
        ssl=use_ssl,
        timeout=60,
    )
    if creds.get("starttls"):
        client.starttls()
    client.login(creds["username"], creds["password"])
    return client


def discover_folders(client: IMAPClient) -> dict[str, object]:
    """Mapeia pastas especiais por flags SPECIAL-USE e fallback por nome.

    `spam` é uma LISTA: alguns servidores/clientes têm mais de uma pasta tipo-spam
    (ex.: `Junk` oficial + `spam` + `INBOX.spam`). Monitoramos todas. Pastas `AI.*`
    do próprio agente são ignoradas na detecção de papel.
    """
    spam: list[str] = []
    sent: str | None = None
    trash: str | None = None
    archive: str | None = None
    for flags, _delim, name in client.list_folders():
        if name == "AI" or name.startswith("AI."):
            continue
        flat = " ".join(f.decode(errors="replace") if isinstance(f, bytes) else str(f) for f in flags)
        if "\\Junk" in flat or SPAM_FOLDER_RE.search(name):
            spam.append(name)
        elif "\\Sent" in flat or (sent is None and SENT_FOLDER_RE.search(name)):
            sent = name
        elif "\\Trash" in flat or (trash is None and TRASH_FOLDER_RE.search(name)):
            trash = name
        elif "\\Archive" in flat or (archive is None and ARCHIVE_FOLDER_RE.search(name)):
            archive = name
    return {
        "inbox": "INBOX",
        "spam": spam,
        "sent": sent,
        "trash": trash,
        "archive": archive,
    }


# Mais restrito que SENT_FOLDER_RE de propósito: aquele casa "sent" no meio de
# qualquer palavra, o que basta quando se escolhe UMA pasta por flag SPECIAL-USE,
# mas na varredura faria uma pasta "Presentations" entrar como caixa de envio —
# e todas as mensagens dela virariam `is_sent_by_user`.
SENT_FOLDER_STRICT_RE = re.compile(r"(?:^|[./\\])(?:sent|enviad\w*)(?:$|[\s./\\])", re.IGNORECASE)


def discover_sent_folders(client: IMAPClient) -> list[str]:
    """TODAS as pastas de envio, não só a primeira.

    `discover_folders` devolve uma única pasta `sent`, o que basta para o sync
    incremental. O catálogo de respostas precisa varrer as variantes que
    convivem na mesma conta (`Sent`, `Sent Items`, `INBOX.Enviados`, arquivos
    por ano). Pastas `AI.*` e as marcadas como lixo/rascunho ficam de fora.
    """
    folders: list[str] = []
    for flags, _delimiter, name in client.list_folders():
        if name == "AI" or name.startswith("AI."):
            continue
        flat = " ".join(f.decode(errors="replace") if isinstance(f, bytes) else str(f) for f in flags)
        if any(marker in flat for marker in ("\\Junk", "\\Trash", "\\Drafts")):
            continue
        if "\\Sent" in flat or SENT_FOLDER_STRICT_RE.search(name):
            folders.append(name)
    return folders


def ensure_archive_folder(client: IMAPClient, fallback: str = "Archive") -> str:
    """Retorna o arquivo nativo do servidor/Canary ou cria um fallback único.

    A flag SPECIAL-USE ``\\Archive`` tem prioridade sobre nomes. Isso reconhece,
    por exemplo, ``Archives`` criado pelo provedor/Canary e evita uma duplicata.
    """
    discovered = discover_folders(client).get("archive")
    if isinstance(discovered, str) and discovered:
        return discovered
    existing = {name for _flags, _delimiter, name in client.list_folders()}
    folder = fallback
    if folder not in existing:
        client.create_folder(folder)
    subscribed = {name for _flags, _delimiter, name in client.list_sub_folders()}
    if folder not in subscribed:
        client.subscribe_folder(folder)
    return folder


def ensure_ai_folders(client: IMAPClient, ai_labels: list[str], delimiter: str = ".") -> dict[str, str]:
    """Cria pastas AI (AI/Spam Suspeito -> AI.Spam Suspeito conforme delimitador) e
    retorna o mapa label -> nome real da pasta."""
    try:
        _flags, delim, _name = client.list_folders()[0]
        delimiter = delim.decode() if isinstance(delim, bytes) else (delim or delimiter)
    except (IndexError, AttributeError):
        pass
    existing = {name for _f, _d, name in client.list_folders()}
    # Clientes (Roundcube/Thunderbird) só exibem pastas ASSINADAS — sempre assinar
    subscribed = {name for _f, _d, name in client.list_sub_folders()}
    mapping: dict[str, str] = {}
    for label in ai_labels:
        folder = label.replace("/", delimiter)
        mapping[label] = folder
        if folder not in existing:
            client.create_folder(folder)
        if folder not in subscribed:
            client.subscribe_folder(folder)
    return mapping
