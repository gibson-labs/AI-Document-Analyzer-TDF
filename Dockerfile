# syntax=docker/dockerfile:1

FROM node:20-alpine AS frontend-build
WORKDIR /frontend
COPY frontend/package.json frontend/package-lock.json* ./
RUN npm install --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

FROM python:3.11-slim AS app
WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    FRONTEND_DIST_DIR=/app/frontend/dist \
    TDF_SESSIONS_ROOT=/tmp/tdf_sessions \
    SESSION_TTL_MINUTES=60

RUN apt-get update && apt-get install -y --no-install-recommends \
    libgl1 \
    libglib2.0-0 \
    poppler-utils \
    tesseract-ocr \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.docker.txt ./
RUN pip install --no-cache-dir -r requirements.docker.txt

COPY server.py ./
COPY file.py ./
COPY app.py ./
COPY text_extraction.py ./
COPY table_agent.py ./
COPY vision_extraction_agent.py ./
COPY storage/ ./storage/
COPY services/ ./services/

COPY --from=frontend-build /frontend/dist/ ./frontend/dist/

EXPOSE 8080
CMD ["python", "-m", "uvicorn", "server:app", "--host", "0.0.0.0", "--port", "8080"]
