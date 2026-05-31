FROM python:3.12-slim

LABEL org.opencontainers.image.title="Machine Doctor"
LABEL org.opencontainers.image.description="Hardware diagnostics for Umbrel nodes"
LABEL org.opencontainers.image.source="https://github.com/AlbercoinDev/Umbrel-AppStore-Albercoin"
LABEL org.opencontainers.image.version="0.1.7"

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    stress-ng \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY src/ ./

EXPOSE 2190

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "2190"]
