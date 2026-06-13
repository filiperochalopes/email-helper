FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install -e ".[dev]"

COPY alembic.ini ./
COPY migrations ./migrations
COPY tests ./tests

CMD ["uvicorn", "email_agent.api.app:app", "--host", "0.0.0.0", "--port", "8000"]
