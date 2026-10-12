FROM python:3.12-slim-bookworm
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    UV_LINK_MODE=copy PATH="/app/.venv/bin:$PATH"
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpango-1.0-0 libpangoft2-1.0-0 libharfbuzz-subset0 \
    && rm -rf /var/lib/apt/lists/* \
    && pip install --no-cache-dir uv==0.13.0 \
    && useradd --uid 10001 --create-home bookcraft
COPY pyproject.toml uv.lock README.md alembic.ini ./
COPY src ./src
COPY data ./data
COPY docs ./docs
COPY migrations ./migrations
COPY scripts/ops/check_sales_connection.py ./scripts/ops/check_sales_connection.py
RUN uv sync --frozen --no-dev --no-editable \
    && mkdir -p reports data/generated \
    && chown -R bookcraft:bookcraft /app
USER bookcraft
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=3)"
CMD ["uvicorn", "bookcraft.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
