from email_agent.intelligence.classifier import classify
from email_agent.intelligence.taxonomy import (
    LABEL_FISCAL,
    LABEL_MARKETING,
    LABEL_REVISAR,
    LABEL_SPAM_SUSPEITO,
)


def _classify(**kwargs):
    defaults = dict(
        subject="", normalized_text="", from_email="a@b.com",
        has_list_unsubscribe=False, attachment_filenames=[], attachment_types=[],
        in_provider_spam=False,
    )
    defaults.update(kwargs)
    return classify(**defaults)


def test_fiscal_pdf():
    r = _classify(
        subject="Nota fiscal de serviço - junho",
        normalized_text="Segue em anexo a nota fiscal e o boleto para pagamento.",
        attachment_filenames=["nfse.pdf"],
    )
    assert LABEL_FISCAL in r.suggested_labels


def test_marketing_promo_newsletter():
    r = _classify(
        subject="Newsletter semanal: promoção imperdível!",
        normalized_text="Cupom de desconto! Frete grátis nas compras acima de R$99.",
        has_list_unsubscribe=True,
        from_email="noreply@loja.com",
    )
    # conteúdo promocional => promocao, mas continua sob a label AI/Marketing
    assert r.category == "promocao"
    assert LABEL_MARKETING in r.suggested_labels


def test_spam_with_malicious_attachment():
    r = _classify(
        subject="Dívida em seu CPF - regularize",
        normalized_text="Sua conta será bloqueada, clique imediatamente e confirme seus dados.",
        attachment_filenames=["boleto.exe"],
    )
    assert r.category == "spam_suspeito"
    assert LABEL_SPAM_SUSPEITO in r.suggested_labels


def test_spam_folder_but_important_goes_to_review():
    r = _classify(
        subject="Contrato assinado - favor validar pagamento",
        normalized_text="Segue contrato com prazo de vencimento. Aprovação necessária.",
        in_provider_spam=True,
    )
    assert r.needs_human_review
    assert LABEL_REVISAR in r.suggested_labels


def test_promocao_vs_marketing():
    r = _classify(
        subject="20% OFF só hoje! Frete grátis",
        normalized_text="Aproveite o cupom de desconto, últimas unidades. Compre agora.",
        has_list_unsubscribe=True,
        from_email="ofertas@loja.com",
    )
    assert r.category == "promocao"


def test_noticia_newsletter():
    r = _classify(
        subject="Nova temporada de Black Mirror já disponível",
        normalized_text="Confira os destaques desta semana e o lançamento da nova série. Leia mais no blog.",
        has_list_unsubscribe=True,
        from_email="newsletter@streaming.com",
    )
    assert r.category == "noticia"


def test_infra_alert_is_important():
    r = _classify(
        subject="ALERTA: backup failed no servidor db01",
        normalized_text="O backup falhou. Erro de certificado SSL. Ação necessária.",
    )
    assert r.priority in ("P0", "P1")
