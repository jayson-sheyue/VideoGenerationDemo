FROM python:3.12-slim

# curl backs the resumable GCS download fallback in app/gcs_util.py
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY static ./static

ENV PORT=8080
EXPOSE 8080

# One worker only: JobStore keeps job state in process memory, so a second
# worker would answer polls for jobs it never started.
CMD exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT} --workers 1
