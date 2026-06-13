"""Camada 1: regras determinísticas de classificação.

Recebe sinais extraídos da mensagem e devolve uma classificação preliminar.
As regras de banco (EmailRule) complementam estas regras de código.
"""
import re
from dataclasses import dataclass, field
from typing import Any

FISCAL_PATTERNS = re.compile(
    r"nota fiscal|nf-?e|nfs-?e|boleto|fatura|cobran[çc]a|recibo|guia|\bdas\b|\binss\b|"
    r"pr[óo]-?labore|imposto|darf|contabilidade|honor[áa]rios",
    re.I,
)
INFRA_PATTERNS = re.compile(
    r"backup (failed|falhou)|certificate|certificado|ssl|uptime|downtime|"
    r"\berror\b|\bfailed\b|\bfalha\b|inativo|fora do ar|servidor|dom[íi]nio.*(expira|venc)",
    re.I,
)
URGENCY_PATTERNS = re.compile(
    r"urgente|pendente|a[çc][ãa]o necess[áa]ria|aprova[çc][ãa]o|vencimento|venc[ee]|"
    r"bloqueio|bloqueada?o?|prazo|[úu]ltimo dia",
    re.I,
)
PAYMENT_CONTRACT_PATTERNS = re.compile(
    r"contrato|pagamento|pagar|transfer[êe]ncia|dep[óo]sito|valida(r|[çc][ãa]o)|assinatura|proposta",
    re.I,
)
SCAM_PATTERNS = re.compile(
    r"sua conta ser[áa] (suspensa|bloqueada)|confirme seus dados|atualize seus dados banc[áa]rios|"
    r"clique imediatamente|pr[êe]mio|voc[êe] foi sorteado|heran[çc]a|bitcoin.*investimento|"
    r"d[íi]vida em seu (cpf|nome)|protesto|serasa.*regularize",
    re.I,
)
RESUME_PATTERNS = re.compile(r"curr[íi]culo|curriculum|\bcv\b|vaga de emprego|oportunidade.*vaga", re.I)
MARKETING_PATTERNS = re.compile(
    r"newsletter|promo[çc][ãa]o|cupom|desconto|oferta|carrinho|frete gr[áa]tis|black friday|imperd[íi]vel",
    re.I,
)
PROMO_PATTERNS = re.compile(
    r"promo[çc][ãa]o|cupom|desconto|\d+%|oferta|carrinho|frete gr[áa]tis|black friday|"
    r"imperd[íi]vel|liquida[çc][ãa]o|[úu]ltimas unidades|compre|aproveite",
    re.I,
)
NEWS_PATTERNS = re.compile(
    r"newsletter|nova (s[ée]rie|temporada|edi[çc][ãa]o)|lan[çc]amento|novidade|"
    r"resumo da semana|digest|artigo|blog|epis[óo]dio|atualiza[çc][ãa]o de conte[úu]do|"
    r"confira|leia mais|destaques",
    re.I,
)
TRACKING_PATTERNS = re.compile(
    r"seu pedido (foi|est[áa])|rastreamento|rastreio|objeto postado|saiu para entrega|a caminho", re.I
)
TRACKING_ACTION_PATTERNS = re.compile(
    r"retirada|retire|pagamento pendente|taxa|imposto de importa[çc][ãa]o|problema na entrega|"
    r"a[çc][ãa]o necess[áa]ria|endere[çc]o insuficiente|devolvido|extraviado",
    re.I,
)
SUSPICIOUS_ATTACHMENT_EXT = (".exe", ".scr", ".js", ".vbs", ".bat", ".cmd", ".jar", ".iso", ".img", ".html")


@dataclass
class RuleResult:
    spam_score: float = 0.0
    importance_score: float = 0.0
    category_votes: dict[str, float] = field(default_factory=dict)
    reasons: list[str] = field(default_factory=list)
    signals: dict[str, Any] = field(default_factory=dict)

    def vote(self, category: str, weight: float, reason: str) -> None:
        self.category_votes[category] = self.category_votes.get(category, 0.0) + weight
        self.reasons.append(reason)


def evaluate_rules(
    *,
    subject: str,
    normalized_text: str,
    from_email: str | None,
    has_list_unsubscribe: bool,
    attachment_filenames: list[str],
    attachment_types: list[str],
    in_provider_spam: bool,
    vip_domains: set[str],
    blocked_domains: set[str],
) -> RuleResult:
    r = RuleResult()
    text = f"{subject}\n{normalized_text}"
    domain = (from_email or "").split("@")[-1].lower()
    has_pdf = any(
        (f or "").lower().endswith(".pdf") for f in attachment_filenames
    ) or any("pdf" in (t or "").lower() for t in attachment_types)
    suspicious_attachment = any(
        (f or "").lower().endswith(SUSPICIOUS_ATTACHMENT_EXT) for f in attachment_filenames
    )
    is_noreply = bool(re.search(r"no-?reply|nao-?responda", from_email or "", re.I))

    r.signals.update(
        domain=domain, has_pdf=has_pdf, suspicious_attachment=suspicious_attachment,
        is_noreply=is_noreply, has_list_unsubscribe=has_list_unsubscribe,
        in_provider_spam=in_provider_spam,
    )

    if domain in blocked_domains:
        r.spam_score += 0.7
        r.vote("spam_suspeito", 0.9, f"domínio bloqueado: {domain}")
    if domain in vip_domains:
        r.importance_score += 40
        r.reasons.append(f"remetente VIP: {domain}")

    if SCAM_PATTERNS.search(text):
        r.spam_score += 0.5
        r.vote("spam_suspeito", 0.6, "padrões de golpe/urgência financeira suspeita")
    if suspicious_attachment:
        r.spam_score += 0.6
        r.vote("spam_suspeito", 0.7, "anexo com extensão potencialmente maliciosa")
    if RESUME_PATTERNS.search(text) and attachment_filenames:
        r.vote("spam_suspeito", 0.4, "possível currículo não solicitado com anexo")

    if FISCAL_PATTERNS.search(text) and has_pdf:
        r.importance_score += 30
        r.vote("documento_fiscal", 0.8, "termos fiscais + PDF anexo")
    elif FISCAL_PATTERNS.search(text):
        r.importance_score += 15
        r.vote("documento_fiscal", 0.4, "termos fiscais no texto, sem PDF")
    elif has_pdf:
        r.vote("documento", 0.5, "PDF anexo sem indicação fiscal")

    if INFRA_PATTERNS.search(text):
        r.importance_score += 35
        r.vote("importante_p1", 0.6, "alerta de infraestrutura/segurança")
    if URGENCY_PATTERNS.search(text):
        r.importance_score += 15
        r.reasons.append("linguagem de prazo/urgência")
    if PAYMENT_CONTRACT_PATTERNS.search(text):
        r.importance_score += 15
        r.reasons.append("menção a pagamento/contrato/decisão")

    is_tracking = bool(TRACKING_PATTERNS.search(text))
    if is_tracking and TRACKING_ACTION_PATTERNS.search(text):
        r.importance_score += 25
        r.vote("importante_p1", 0.5, "rastreio de transporte exigindo ação")
    elif is_tracking:
        r.vote("marketing", 0.5, "rastreio meramente informativo")

    if has_list_unsubscribe or MARKETING_PATTERNS.search(text):
        r.importance_score -= 15
        if PROMO_PATTERNS.search(text):
            r.vote("promocao", 0.6, "promoção/desconto/oferta")
        elif NEWS_PATTERNS.search(text):
            r.vote("noticia", 0.6, "newsletter/conteúdo informativo")
        else:
            r.vote("marketing", 0.5, "List-Unsubscribe/linguagem de marketing")
    if is_noreply:
        r.importance_score -= 10
        r.reasons.append("remetente noreply")

    # Conflito clássico: spam do provedor + sinais de importância => revisar
    if in_provider_spam and r.importance_score >= 25:
        r.vote("revisar", 1.0, "no Spam do provedor mas com sinais de importância")

    r.importance_score = max(0.0, min(100.0, r.importance_score))
    r.spam_score = max(0.0, min(1.0, r.spam_score))
    return r
