FROM python:3.12-slim

WORKDIR /app/backend

COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY backend/ .
COPY frontend/ /app/frontend/

RUN chmod 755 /app/backend

RUN useradd -m -r appuser && chown -R appuser:appuser /app

HEALTHCHECK --interval=60s --timeout=10s CMD curl -f http://localhost:8001/health || exit 1

ENV PORT=8001

USER appuser

CMD python -m uvicorn main:app --host 0.0.0.0 --port ${PORT}
