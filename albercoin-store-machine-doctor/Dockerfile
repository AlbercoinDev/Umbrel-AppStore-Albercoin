FROM python:3.12-slim@sha256:5e2bdb1b0d542d5039cffb8181c65930093949194a65c365dfb1b0fe13a4fa5b

LABEL org.opencontainers.image.title="Machine Doctor"
LABEL org.opencontainers.image.description="Hardware diagnostics for Umbrel nodes"
LABEL org.opencontainers.image.source="https://github.com/AlbercoinDev/Umbrel-AppStore-Albercoin"
LABEL org.opencontainers.image.version="0.1.10"

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    stress-ng \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY src/ ./

EXPOSE 2190

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "2190"]
