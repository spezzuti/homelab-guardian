FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY homelab_guardian ./homelab_guardian
COPY config.example.yaml ./config.example.yaml

RUN mkdir -p /app/data /app/reports

CMD ["python", "-m", "homelab_guardian.main", "--config", "/app/config.yaml"]
