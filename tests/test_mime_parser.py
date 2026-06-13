from email.message import EmailMessage

from email_agent.parsing.mime_parser import dedupe_fingerprint, parse_mime_bytes


def _build_email(subject="Teste", body="Corpo do e-mail", html=None, attach_pdf=False) -> bytes:
    msg = EmailMessage()
    msg["From"] = "Fulano <fulano@exemplo.com>"
    msg["To"] = "filipe@noharm.ai"
    msg["Subject"] = subject
    msg["Message-ID"] = "<abc123@exemplo.com>"
    msg["Date"] = "Thu, 11 Jun 2026 10:00:00 -0300"
    msg.set_content(body)
    if html:
        msg.add_alternative(html, subtype="html")
    if attach_pdf:
        msg.add_attachment(b"%PDF-1.4 fake", maintype="application", subtype="pdf",
                           filename="nota_fiscal.pdf")
    return msg.as_bytes()


def test_parse_basic():
    parsed = parse_mime_bytes(_build_email())
    assert parsed.from_email == "fulano@exemplo.com"
    assert parsed.subject == "Teste"
    assert parsed.message_id_header == "<abc123@exemplo.com>"
    assert "Corpo do e-mail" in parsed.normalized_text
    assert not parsed.has_attachment


def test_parse_attachment():
    parsed = parse_mime_bytes(_build_email(attach_pdf=True))
    assert parsed.has_attachment
    assert parsed.attachments[0].filename == "nota_fiscal.pdf"
    assert parsed.attachments[0].sha256


def test_fingerprint_stable():
    a = parse_mime_bytes(_build_email())
    b = parse_mime_bytes(_build_email())
    assert dedupe_fingerprint(a) == dedupe_fingerprint(b)
