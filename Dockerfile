# DraftLab data job — NOT the Horizon MCP image.
FROM python:3.12-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    ARTIFACTS_DIR=/data/artifacts

COPY requirements.txt requirements-data.txt ./
RUN pip install --no-cache-dir -r requirements-data.txt

COPY sleeper_core ./sleeper_core
COPY tools ./tools
COPY data_api ./data_api
COPY playcaller_tiers.json ./playcaller_tiers.json

RUN mkdir -p /data/artifacts

EXPOSE 8080

CMD ["uvicorn", "data_api.app:app", "--host", "0.0.0.0", "--port", "8080", "--timeout-keep-alive", "120"]
