from email.message import EmailMessage

from email_agent.parsing.mime_parser import dedupe_fingerprint, parse_mime_bytes


def _build_email(subject="Teste", body="Corpo do e-mail", html=None, attach_pdf=False,
                 in_reply_to=None, references=None) -> bytes:
    msg = EmailMessage()
    msg["From"] = "Fulano <fulano@exemplo.com>"
    msg["To"] = "filipe@noharm.ai"
    msg["Subject"] = subject
    msg["Message-ID"] = "<abc123@exemplo.com>"
    msg["Date"] = "Thu, 11 Jun 2026 10:00:00 -0300"
    if in_reply_to:
        msg["In-Reply-To"] = in_reply_to
    if references:
        msg["References"] = references
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


def test_plain_text_preserves_paragraphs_and_lists():
    body = "Primeiro parágrafo.\n\n- item um\n- item dois\n\nÚltimo parágrafo."
    parsed = parse_mime_bytes(_build_email(body=body))

    assert "Primeiro parágrafo.\n\n- item um\n- item dois\n\nÚltimo parágrafo." in parsed.normalized_text


def test_parse_attachment():
    parsed = parse_mime_bytes(_build_email(attach_pdf=True))
    assert parsed.has_attachment
    assert parsed.attachments[0].filename == "nota_fiscal.pdf"
    assert parsed.attachments[0].sha256


def test_fingerprint_stable():
    a = parse_mime_bytes(_build_email())
    b = parse_mime_bytes(_build_email())
    assert dedupe_fingerprint(a) == dedupe_fingerprint(b)


def test_parse_reply_headers_for_imap_threading():
    parsed = parse_mime_bytes(_build_email(
        in_reply_to="<parent@example.com>",
        references="<root@example.com> <parent@example.com>",
    ))
    assert parsed.in_reply_to_header == "<parent@example.com>"
    assert parsed.references == ["<root@example.com>", "<parent@example.com>"]
