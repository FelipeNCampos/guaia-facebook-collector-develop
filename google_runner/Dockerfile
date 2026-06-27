FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/workspace/google_runner:/workspace/shared/python

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        chromium \
        chromium-driver \
        procps \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /workspace

COPY google_runner/requirements.txt /workspace/google_runner/requirements.txt
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r /workspace/google_runner/requirements.txt

COPY google_runner /workspace/google_runner
COPY shared /workspace/shared
COPY db /workspace/db

WORKDIR /workspace/google_runner

CMD ["python", "-m", "app.main"]

