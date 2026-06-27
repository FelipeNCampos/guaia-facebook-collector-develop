FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /workspace/feeder

COPY feeder/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY feeder/ .

CMD ["python", "facebook_api_feeder.py"]
