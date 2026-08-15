"""Parser MIME: bytes brutos (RFC 822) -> dicionário normalizado."""
import hashlib
import re
from dataclasses import dataclass, field
from datetime import datetime
from email import message_from_bytes, policy
from email.message import EmailMessage as PyEmailMessage
from email.utils import getaddresses, parsedate_to_datetime

from email_agent.parsing.html_cleaner import clean_html_to_text


def _normalize_plain_text(text: str, max_chars: int) -> str:
    """Remove ruído horizontal sem destruir parágrafos, listas e citações."""
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    normalized = "\n".join(re.sub(r"[ \t]+", " ", line).strip() for line in normalized.split("\n"))
    normalized = re.sub(r"\n{3,}", "\n\n", normalized).strip()
    return normalized[:max_chars]


@dataclass
class ParsedAttachment:
    filename: str | None
    content_type: str | None
    size_bytes: int
    sha256: str


@dataclass
class ParsedEmail:
    message_id_header: str | None
    in_reply_to_header: str | None
    references: list[str]
    from_email: str | None
    from_name: str | None
    to: list[str]
    cc: list[str]
    subject: str
    date: datetime | None
    text_plain: str | None
    text_html: str | None
    normalized_text: str
    normalized_text_hash: str
    has_list_unsubscribe: bool
    attachments: list[ParsedAttachment] = field(default_factory=list)

    @property
    def has_attachment(self) -> bool:
        return bool(self.attachments)


def parse_mime_bytes(raw: bytes, max_chars: int = 12000) -> ParsedEmail:
    msg: PyEmailMessage = message_from_bytes(raw, policy=policy.default)

    froms = getaddresses([msg.get("From", "")])
    from_name, from_email = (froms[0] if froms else (None, None))
    to = [addr for _, addr in getaddresses([msg.get("To", "")]) if addr]
    cc = [addr for _, addr in getaddresses([msg.get("Cc", "")]) if addr]

    date = None
    if msg.get("Date"):
        try:
            date = parsedate_to_datetime(msg["Date"])
        except (ValueError, TypeError):
            date = None

    text_plain: str | None = None
    text_html: str | None = None
    attachments: list[ParsedAttachment] = []

    for part in msg.walk():
        if part.is_multipart():
            continue
        ctype = part.get_content_type()
        disposition = part.get_content_disposition()
        if disposition == "attachment" or (part.get_filename() and disposition != "inline"):
            payload = part.get_payload(decode=True) or b""
            attachments.append(
                ParsedAttachment(
                    filename=part.get_filename(),
                    content_type=ctype,
                    size_bytes=len(payload),
                    sha256=hashlib.sha256(payload).hexdigest(),
                )
            )
        elif ctype == "text/plain" and text_plain is None:
            text_plain = part.get_content()
        elif ctype == "text/html" and text_html is None:
            text_html = part.get_content()

    if text_plain and text_plain.strip():
        normalized = _normalize_plain_text(text_plain, max_chars)
    elif text_html:
        normalized = clean_html_to_text(text_html, max_chars=max_chars)
    else:
        normalized = ""

    return ParsedEmail(
        message_id_header=(msg.get("Message-ID") or "").strip() or None,
        in_reply_to_header=(msg.get("In-Reply-To") or "").strip() or None,
        references=(msg.get("References") or "").split(),
        from_email=(from_email or "").lower() or None,
        from_name=from_name or None,
        to=to,
        cc=cc,
        subject=msg.get("Subject", "") or "",
        date=date,
        text_plain=text_plain,
        text_html=text_html,
        normalized_text=normalized,
        normalized_text_hash=hashlib.sha256(normalized.encode()).hexdigest(),
        has_list_unsubscribe=bool(msg.get("List-Unsubscribe")),
        attachments=attachments,
    )


def dedupe_fingerprint(parsed: ParsedEmail) -> str:
    """Fingerprint estável para deduplicação quando UID muda entre pastas IMAP."""
    if parsed.message_id_header:
        basis = parsed.message_id_header
    else:
        subject_norm = " ".join((parsed.subject or "").lower().split())
        date_part = parsed.date.strftime("%Y%m%d%H%M") if parsed.date else ""
        basis = f"{parsed.from_email}|{subject_norm}|{date_part}|{parsed.normalized_text_hash}"
    return hashlib.sha256(basis.encode()).hexdigest()
