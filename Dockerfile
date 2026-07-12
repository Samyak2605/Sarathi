FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Pre-fetch the semantic cache's embedding model at build time so the
# first real request in production doesn't pay a ~20s cold download.
RUN python -c "from gateway.cache.embeddings import get_embedding_provider; get_embedding_provider().embed('warmup')"

ENV SARATHI_MODE=local
EXPOSE 8000

CMD ["uvicorn", "gateway.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
