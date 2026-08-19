"""Dois labels opcionais no provedor; todo o restante é estado local."""

# `Archive` não aparece aqui: é uma pasta/ação nativa do provedor.
LABEL_FOCO = "AI/Foco"
LABEL_SPAM_SUSPEITO = "AI/Spam Suspeito"

ALL_AI_LABELS = [
    LABEL_FOCO,
    LABEL_SPAM_SUSPEITO,
]

# --- Política de organização: mover (não copiar) ---
# Como o Gmail usa labels reais e o IMAP só tem pastas, "aplicar label" agora MOVE
# o e-mail para fora da INBOX (Gmail: adiciona label + remove INBOX; IMAP: move para
# a pasta AI.…), EXCETO as labels abaixo, que FICAM na INBOX por exigirem ação do
# usuário. Assim o mesmo e-mail nunca aparece em dois lugares.
INBOX_KEEP_LABELS = {LABEL_FOCO}

# No IMAP um e-mail vive em UMA pasta só. Quando a classificação sugere mais de uma
# label que sai da INBOX, esta ordem decide a pasta destino (mais específica/forte
# primeiro). As demais labels ficam registradas em email_message.ai_labels (banco).
IMAP_DEST_PRIORITY = [
    LABEL_SPAM_SUSPEITO,
]


def moves_out_of_inbox(label: str) -> bool:
    """True se aplicar esta label deve tirar o e-mail da INBOX."""
    return label in ALL_AI_LABELS and label not in INBOX_KEEP_LABELS


def imap_keyword(label: str) -> str:
    """Keyword IMAP (flag personalizada) equivalente à label, p/ as que FICAM na INBOX.
    Ex.: 'AI/Foco' -> 'AI_Foco'.
    Sem '/' nem espaço (caracteres problemáticos em atom IMAP/clientes)."""
    return label.replace("/", "_").replace(" ", "_")


def imap_destination(labels: list[str]) -> str | None:
    """Escolhe a pasta IMAP destino dado o conjunto de labels sugeridas.
    Retorna None se nenhuma label move o e-mail para fora da INBOX."""
    for candidate in IMAP_DEST_PRIORITY:
        if candidate in labels:
            return candidate
    return None

# Categorias internas para busca e apresentação
CATEGORIES = [
    "spam_suspeito",
    "documento",
    "documento_fiscal",
    "marketing",
    "noticia",       # newsletter/conteúdo informativo (nova série, artigo, novidade)
    "promocao",      # desconto, cupom, oferta, carrinho abandonado
    # Duas direções, não uma: "quem deve o próximo e-mail" muda a ação.
    "aguardando_minha_resposta",       # terceiro escreveu, eu não respondi
    "aguardando_resposta_de_terceiro",  # eu escrevi, ninguém respondeu
    "followup_sem_acao",
    "revisar",
    "importante_p0",
    "importante_p1",
    "ignorar",
]

PRIORITIES = ["P0", "P1", "P2", "ignore"]
