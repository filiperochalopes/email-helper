"""Labels AI, categorias internas e prioridades do MVP."""

# Labels AI (espelhadas como labels no Gmail e pastas no IMAP)
LABEL_IMPORTANTE = "AI/Importante"
LABEL_AGUARDANDO = "AI/Importante/Aguardando Resposta"
LABEL_SPAM_SUSPEITO = "AI/Spam Suspeito"
LABEL_FRAUDE = "AI/Spam Suspeito/Fraude"  # impersonação de remetente (sub-label)
LABEL_DOCUMENTOS = "AI/Documentos"
LABEL_FISCAL = "AI/Documentos/Fiscal"
LABEL_MARKETING = "AI/Marketing"
LABEL_REVISAR = "AI/Revisar"
LABEL_LIXO_SUGERIDO = "AI/Lixo Sugerido"
LABEL_ARCHIVE = "AI/Archive"  # arquivo morto: conteúdo relevante e antigo, fora da INBOX

ALL_AI_LABELS = [
    LABEL_IMPORTANTE,
    LABEL_AGUARDANDO,
    LABEL_SPAM_SUSPEITO,
    LABEL_FRAUDE,
    LABEL_DOCUMENTOS,
    LABEL_FISCAL,
    LABEL_MARKETING,
    LABEL_REVISAR,
    LABEL_LIXO_SUGERIDO,
    LABEL_ARCHIVE,
]

# --- Política de organização: mover (não copiar) ---
# Como o Gmail usa labels reais e o IMAP só tem pastas, "aplicar label" agora MOVE
# o e-mail para fora da INBOX (Gmail: adiciona label + remove INBOX; IMAP: move para
# a pasta AI.…), EXCETO as labels abaixo, que FICAM na INBOX por exigirem ação do
# usuário. Assim o mesmo e-mail nunca aparece em dois lugares.
INBOX_KEEP_LABELS = {LABEL_IMPORTANTE, LABEL_AGUARDANDO}

# No IMAP um e-mail vive em UMA pasta só. Quando a classificação sugere mais de uma
# label que sai da INBOX, esta ordem decide a pasta destino (mais específica/forte
# primeiro). As demais labels ficam registradas em email_message.ai_labels (banco).
IMAP_DEST_PRIORITY = [
    LABEL_FRAUDE,
    LABEL_SPAM_SUSPEITO,
    LABEL_FISCAL,
    LABEL_DOCUMENTOS,
    LABEL_ARCHIVE,
    LABEL_REVISAR,
    LABEL_MARKETING,
    LABEL_LIXO_SUGERIDO,
]


def moves_out_of_inbox(label: str) -> bool:
    """True se aplicar esta label deve tirar o e-mail da INBOX."""
    return label in ALL_AI_LABELS and label not in INBOX_KEEP_LABELS


def imap_keyword(label: str) -> str:
    """Keyword IMAP (flag personalizada) equivalente à label, p/ as que FICAM na INBOX.
    Ex.: 'AI/Importante/Aguardando Resposta' -> 'AI_Importante_Aguardando_Resposta'.
    Sem '/' nem espaço (caracteres problemáticos em atom IMAP/clientes)."""
    return label.replace("/", "_").replace(" ", "_")


def imap_destination(labels: list[str]) -> str | None:
    """Escolhe a pasta IMAP destino dado o conjunto de labels sugeridas.
    Retorna None se nenhuma label move o e-mail para fora da INBOX."""
    for candidate in IMAP_DEST_PRIORITY:
        if candidate in labels:
            return candidate
    return None

# Categorias internas (treino, Label Studio, busca)
CATEGORIES = [
    "spam_suspeito",
    "documento",
    "documento_fiscal",
    "marketing",
    "noticia",       # newsletter/conteúdo informativo (nova série, artigo, novidade)
    "promocao",      # desconto, cupom, oferta, carrinho abandonado
    "aguardando_resposta",
    "revisar",
    "importante_p0",
    "importante_p1",
    "ignorar",
]

PRIORITIES = ["P0", "P1", "P2", "ignore"]

CATEGORY_TO_LABELS: dict[str, list[str]] = {
    "spam_suspeito": [LABEL_SPAM_SUSPEITO],
    "documento": [LABEL_DOCUMENTOS],
    "documento_fiscal": [LABEL_DOCUMENTOS, LABEL_FISCAL],
    "marketing": [LABEL_MARKETING],
    "noticia": [LABEL_MARKETING],
    "promocao": [LABEL_MARKETING],
    "aguardando_resposta": [LABEL_AGUARDANDO],
    "revisar": [LABEL_REVISAR],
    "importante_p0": [LABEL_IMPORTANTE],
    "importante_p1": [LABEL_IMPORTANTE],
    "ignorar": [],
}
