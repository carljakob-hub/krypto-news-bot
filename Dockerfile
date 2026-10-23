FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    NEWSBOT_HOME=/app

WORKDIR /app

COPY requirements.txt requirements-dev.txt ./
RUN pip install -r requirements-dev.txt

COPY pyproject.toml ./
COPY src ./src
COPY tests ./tests
COPY config ./config
RUN pip install --no-deps . \
    && useradd --create-home --uid 10001 newsbot \
    && mkdir -p /app/data \
    && chown -R newsbot:newsbot /app/data

USER newsbot

HEALTHCHECK --interval=5m --timeout=20s --start-period=10m --retries=2 \
    CMD ["newsbot", "health"]

CMD ["newsbot", "run"]
