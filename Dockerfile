FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Phase 6 hardening: run as a non-root user.
RUN useradd -m -u 10001 appuser && chown -R appuser:appuser /app
USER appuser

EXPOSE 8000 8501

# Phase 6: container health probe against the readiness endpoint.
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health/ready', timeout=3).status == 200 else 1)"

# Migrations run first (Alembic); init_db() create_all remains as a dev fallback.
CMD ["sh", "-c", "alembic upgrade head && uvicorn backend.main:app --host 0.0.0.0 --port 8000"]
