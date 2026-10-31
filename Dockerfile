FROM python:3.12-slim AS builder

ENV PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY requirements.txt .
RUN python -m venv /venv \
    && /venv/bin/pip install -r requirements.txt

FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/app/src \
    HF_HOME=/app/data/hf \
    PATH=/venv/bin:$PATH

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends libpq5 \
    && rm -rf /var/lib/apt/lists/*

COPY --from=builder /venv /venv

COPY src ./src
COPY curator ./curator
COPY wsgi.py .

RUN mkdir -p /app/data

EXPOSE 8000

CMD python -c "from agent import secret_store; secret_store.get_session_secret()" \
    && exec gunicorn wsgi:app \
       --worker-class uvicorn.workers.UvicornWorker \
       --workers 2 \
       --bind 0.0.0.0:8000 \
       --timeout 120
