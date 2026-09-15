FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY requirements.lock pyproject.toml README.md ./
RUN python -m pip install --no-cache-dir \
        --index-url https://download.pytorch.org/whl/cpu \
        torch==2.14.0 && \
    python -m pip install --no-cache-dir -r requirements.lock

COPY app ./app
COPY migrations ./migrations
COPY scripts ./scripts
COPY policies ./policies
COPY alembic.ini ./alembic.ini
RUN python -m pip install --no-cache-dir --no-deps .

RUN groupadd --system resolvex && \
    useradd --system --gid resolvex --home-dir /app resolvex && \
    mkdir -p /data/chroma && \
    chown -R resolvex:resolvex /app /data/chroma

USER resolvex

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
