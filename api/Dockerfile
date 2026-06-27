FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/workspace/api:/workspace/shared/python

WORKDIR /workspace

COPY api/requirements.txt /workspace/api/requirements.txt
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r /workspace/api/requirements.txt

COPY api /workspace/api
COPY shared /workspace/shared
COPY db /workspace/db

WORKDIR /workspace/api

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080"]

