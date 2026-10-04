# syntax=docker/dockerfile:1.22
FROM python:3.14-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# -- Build stage: install only production dependencies into an isolated prefix.
FROM base AS builder

COPY pyproject.toml ./
COPY README.md ./
COPY bot ./bot

RUN pip install --no-cache-dir --prefix=/install . \
    && find /install -type d \( -name tests -o -name test \) -prune -exec rm -rf {} +

# -- Final stage: small, non-root runtime image.
FROM base AS final

RUN groupadd --system --gid 1000 app \
    && useradd --system --uid 1000 --gid app --home-dir /app app

COPY --from=builder /install /usr/local
COPY alembic.ini ./
COPY migrations ./migrations
COPY bot ./bot

RUN mkdir -p /app/logs && chown -R app:app /app

USER app

CMD ["sh", "-c", "alembic upgrade head && python -m bot.main"]
