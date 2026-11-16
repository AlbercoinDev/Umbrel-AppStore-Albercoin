FROM python:3.14-slim@sha256:9aaa474097d094040649f2162a627c0049b408f52138d980916788d80f04802f

LABEL org.opencontainers.image.title="Machine Doctor"
LABEL org.opencontainers.image.description="Hardware diagnostics for Umbrel nodes"
LABEL org.opencontainers.image.source="https://github.com/AlbercoinDev/Umbrel-AppStore-Albercoin"
LABEL org.opencontainers.image.version="0.1.11"

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    stress-ng \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY src/ ./

EXPOSE 2190

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "2190"]
