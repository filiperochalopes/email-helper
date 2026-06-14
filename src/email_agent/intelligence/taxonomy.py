"""Labels AI, categorias internas e prioridades do MVP."""

# Labels AI (espelhadas como labels no Gmail e pastas no IMAP)
LABEL_IMPORTANTE = "AI/Importante"
LABEL_AGUARDANDO = "AI/Importante/Aguardando Resposta"
LABEL_SPAM_SUSPEITO = "AI/Spam Suspeito"
LABEL_DOCUMENTOS = "AI/Documentos"
LABEL_FISCAL = "AI/Documentos/Fiscal"
LABEL_MARKETING = "AI/Marketing"
LABEL_REVISAR = "AI/Revisar"
LABEL_LIXO_SUGERIDO = "AI/Lixo Sugerido"

ALL_AI_LABELS = [
    LABEL_IMPORTANTE,
    LABEL_AGUARDANDO,
    LABEL_SPAM_SUSPEITO,
    LABEL_DOCUMENTOS,
    LABEL_FISCAL,
    LABEL_MARKETING,
    LABEL_REVISAR,
    LABEL_LIXO_SUGERIDO,
]

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
