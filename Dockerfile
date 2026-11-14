# Backend image for Render / any Docker-based host.
# Build context = REPO ROOT (not backend/).
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=10000

WORKDIR /srv

COPY backend/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY backend/app ./app
COPY backend/scripts ./scripts
# Prebuilt catalog (16 MB): startup does not rebuild the index.
COPY backend/data/catalog.json ./data/catalog.json

# Render injects $PORT; shell form so it expands at runtime.
EXPOSE 10000
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-10000} --workers 1"]
