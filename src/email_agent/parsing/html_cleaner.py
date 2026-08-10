"""Limpeza de HTML de e-mails com BeautifulSoup4."""
import re

from bs4 import BeautifulSoup

_INVISIBLE_STYLE = re.compile(
    r"display\s*:\s*none|visibility\s*:\s*hidden|font-size\s*:\s*0|opacity\s*:\s*0(?:[^.]|$)", re.IGNORECASE
)


def clean_html_to_text(html: str, max_chars: int = 12000) -> str:
    soup = BeautifulSoup(html, "lxml")

    for tag in soup(["script", "style", "head", "meta", "link", "noscript", "template"]):
        tag.decompose()

    # Tracking pixels e elementos invisíveis
    for img in soup.find_all("img"):
        w, h = img.get("width", ""), img.get("height", "")
        if str(w) in ("0", "1") or str(h) in ("0", "1"):
            img.decompose()
        else:
            alt = img.get("alt") or ""
            img.replace_with(f"[img: {alt}]" if alt.strip() else "")
    for el in soup.find_all(style=_INVISIBLE_STYLE):
        el.decompose()

    # Preservar links relevantes em formato legível
    for a in soup.find_all("a"):
        text = a.get_text(" ", strip=True)
        href = a.get("href", "")
        if href.startswith("http") and text and text.lower() not in href.lower():
            a.replace_with(f"{text} <{href[:120]}>")
        else:
            a.replace_with(text)

    text = soup.get_text("\n")
    text = re.sub(r"[ \t\xa0]+", " ", text)
    text = re.sub(r"\n\s*\n+", "\n\n", text)
    text = text.strip()
    return text[:max_chars]
