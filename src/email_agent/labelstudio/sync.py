"""Sincronização automática com o Label Studio via API (label-studio-sdk).

Fluxo (opção B, sem upload manual de arquivo):

1. ``push_pending_tasks`` — seleciona as mensagens que precisam de olho humano
   (AI/Revisar, AI/Spam Suspeito, AI/Lixo Sugerido, baixa confiança e uma amostra
   de P0/P1), envia como tasks **com pré-anotação** (a sugestão do agente já vem
   marcada, você só confirma/corrige) e registra em ``human_review`` para não
   reenviar.
2. ``pull_annotations`` — lê as tasks já anotadas e cria ``EmailTrainingEvent``
   confiáveis (``source="label_studio"``), que o ``fit_spam_model`` consome.

Ambos rodam na manutenção noturna do Celery (polling) e também via CLI.
A conexão usa ``LABEL_STUDIO_URL`` + ``LABEL_STUDIO_API_KEY``. Se não houver
chaves, as funções viram no-op (não quebram a rotina).
"""
from pathlib import Path

from sqlalchemy import select

from email_agent.config import get_settings
from email_agent.intelligence.taxonomy import (
    CATEGORIES,
    LABEL_LIXO_SUGERIDO,
    LABEL_REVISAR,
    LABEL_SPAM_SUSPEITO,
)
from email_agent.labelstudio.export import build_task_data
from email_agent.logging_setup import get_logger
from email_agent.models import (
    EmailClassification,
    EmailMessage,
    EmailTrainingEvent,
    HumanReview,
    db_session,
)

log = get_logger(__name__)

REVIEW_TYPE = "labelstudio_sync"
MODEL_VERSION = "rules+sgd+ollama"
# Labels AI que, por si só, mandam o e-mail para a fila de anotação.
_SCOPE_LABELS = {LABEL_REVISAR, LABEL_SPAM_SUSPEITO, LABEL_LIXO_SUGERIDO}

_SCHEMA = Path(__file__).with_name("schema.xml").read_text(encoding="utf-8")


def _client():
    """Cria o cliente Label Studio, ou None se não configurado."""
    settings = get_settings()
    if not settings.label_studio_enabled:
        return None
    try:
        from label_studio_sdk import LabelStudio

        return LabelStudio(
            base_url=settings.label_studio_url, api_key=settings.label_studio_api_key
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("labelstudio_init_failed", error=str(exc))
        return None


def ensure_project(client) -> int:
    """Resolve o id do projeto: usa LABEL_STUDIO_PROJECT_ID se setado, senão
    procura por título e cria se não existir."""
    settings = get_settings()
    if settings.label_studio_project_id:
        return settings.label_studio_project_id
    title = settings.label_studio_project_title
    for proj in client.projects.list(title=title):
        if getattr(proj, "title", None) == title:
            return proj.id
    created = client.projects.create(title=title, label_config=_SCHEMA)
    log.info("labelstudio_project_created", project_id=created.id, title=title)
    return created.id


def _prediction(cls: EmailClassification) -> dict:
    """Pré-anotação a partir da sugestão do agente (você só confirma/corrige)."""
    return {
        "model_version": MODEL_VERSION,
        "score": cls.confidence or 0.0,
        "result": [
            {
                "from_name": "category",
                "to_name": "body",
                "type": "choices",
                "value": {"choices": [cls.category]},
            }
        ],
    }


def _select_candidates(session, limit: int) -> list[tuple[EmailMessage, EmailClassification]]:
    """Mensagens que precisam de revisão humana e ainda não foram enviadas."""
    settings = get_settings()
    already = {
        r[0]
        for r in session.execute(
            select(HumanReview.message_id).where(HumanReview.review_type == REVIEW_TYPE)
        )
    }
    rows = session.execute(
        select(EmailMessage, EmailClassification)
        .join(EmailClassification, EmailClassification.message_id == EmailMessage.id)
        .order_by(EmailMessage.id.desc())
        .limit(limit * 5)  # folga: filtramos por escopo em Python
    ).all()

    selected: list[tuple[EmailMessage, EmailClassification]] = []
    priority_quota = settings.label_studio_priority_sample
    for msg, cls in rows:
        if msg.id in already:
            continue
        labels = set(msg.ai_labels or [])
        low_conf = (cls.confidence or 1.0) < settings.label_studio_low_confidence
        is_priority = cls.priority in ("P0", "P1")
        if labels & _SCOPE_LABELS or low_conf:
            selected.append((msg, cls))
        elif is_priority and priority_quota > 0:
            selected.append((msg, cls))
            priority_quota -= 1
        if len(selected) >= limit:
            break
    return selected


def push_pending_tasks(limit: int = 200) -> int:
    """Envia mensagens pendentes ao Label Studio como tasks pré-anotadas."""
    client = _client()
    if client is None:
        log.info("labelstudio_push_skipped", reason="not_configured")
        return 0
    project_id = ensure_project(client)

    with db_session() as session:
        candidates = _select_candidates(session, limit)
        if not candidates:
            return 0
        request = [
            {"data": build_task_data(msg, cls), "predictions": [_prediction(cls)]}
            for msg, cls in candidates
        ]
        resp = client.projects.import_tasks(
            project_id, request=request, return_task_ids=True
        )
        task_ids = list(getattr(resp, "task_ids", None) or [])
        for i, (msg, _cls) in enumerate(candidates):
            ls_task_id = task_ids[i] if i < len(task_ids) else None
            session.add(
                HumanReview(
                    message_id=msg.id,
                    review_type=REVIEW_TYPE,
                    status="exported_labelstudio",
                    proposed_action_json={
                        "ls_project_id": project_id,
                        "ls_task_id": ls_task_id,
                    },
                )
            )
    log.info("labelstudio_pushed", count=len(candidates), project_id=project_id)
    return len(candidates)


def _extract_choice(annotations: list) -> str | None:
    """Primeira escolha de categoria nas anotações de uma task (dict ou objeto SDK)."""
    for ann in annotations or []:
        results = ann.get("result") if isinstance(ann, dict) else getattr(ann, "result", None)
        for res in results or []:
            value = res.get("value", {}) if isinstance(res, dict) else getattr(res, "value", {}) or {}
            choices = value.get("choices") if isinstance(value, dict) else getattr(value, "choices", None)
            if choices:
                return choices[0]
    return None


def pull_annotations() -> int:
    """Lê tasks anotadas no Label Studio e cria eventos de treino confiáveis."""
    client = _client()
    if client is None:
        log.info("labelstudio_pull_skipped", reason="not_configured")
        return 0
    project_id = ensure_project(client)

    created = 0
    with db_session() as session:
        processed = {
            r[0]
            for r in session.execute(
                select(EmailTrainingEvent.reason).where(
                    EmailTrainingEvent.source == "label_studio"
                )
            )
            if r[0] and r[0].startswith("ls_task:")
        }
        tasks = client.tasks.list(project=project_id, only_annotated=True, fields="all")
        for task in tasks:
            marker = f"ls_task:{task.id}"
            if marker in processed:
                continue
            data = task.data if isinstance(task.data, dict) else dict(task.data or {})
            email_agent_id = data.get("email_agent_id")
            label = _extract_choice(getattr(task, "annotations", None))
            if not email_agent_id or label not in CATEGORIES:
                continue
            msg = session.execute(
                select(EmailMessage).where(EmailMessage.email_agent_id == email_agent_id)
            ).scalar_one_or_none()
            if msg is None:
                continue
            session.add(
                EmailTrainingEvent(
                    message_id=msg.id,
                    label=label,
                    source="label_studio",
                    weight=1.0,
                    trusted=True,
                    reason=marker,
                )
            )
            created += 1
    log.info("labelstudio_pulled", created=created, project_id=project_id)
    return created
