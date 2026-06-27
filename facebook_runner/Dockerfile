FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/workspace/facebook_runner:/workspace/shared/python

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        chromium \
        chromium-driver \
        procps \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /workspace

COPY facebook_runner/requirements.txt /workspace/facebook_runner/requirements.txt
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r /workspace/facebook_runner/requirements.txt

COPY facebook_runner /workspace/facebook_runner
COPY shared /workspace/shared
COPY db /workspace/db

WORKDIR /workspace/facebook_runner

CMD ["python", "-m", "app.main"]
