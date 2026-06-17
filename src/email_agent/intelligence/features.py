"""Texto de entrada (features) dos modelos sklearn.

Centralizado para garantir que **treino e predição usem exatamente o mesmo
formato** — qualquer divergência aqui degrada silenciosamente o modelo.

Além de assunto + corpo, incluímos o **remetente** (nome exibido + domínio) como
tokens: é o que permite o modelo aprender padrões de fraude/impersonação por
remetente, não só pelo texto. O domínio vira um token único (`dom_x_y_z`) para o
HashingVectorizer aprender domínios específicos.
"""


def email_features(
    subject: str | None,
    normalized_text: str | None,
    from_email: str | None = None,
    from_name: str | None = None,
) -> str:
    domain = (from_email or "").split("@")[-1].lower()
    parts: list[str] = []
    if from_name:
        parts.append(f"remetente {from_name}")
    if domain:
        parts.append(f"dom_{domain.replace('.', '_')}")
    parts.append(subject or "")
    parts.append(normalized_text or "")
    return "\n".join(parts)
