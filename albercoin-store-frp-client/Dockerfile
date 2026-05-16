FROM python:3.11-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    procps \
    curl \
    && rm -rf /var/lib/apt/lists/*

ARG FRP_VERSION=0.54.0
RUN ARCH=$(uname -m) && \
    if [ "$ARCH" = "aarch64" ]; then ARCH="arm64"; \
    elif [ "$ARCH" = "armv7l" ]; then ARCH="arm"; \
    elif [ "$ARCH" = "x86_64" ]; then ARCH="amd64"; \
    fi && \
    curl -fsSL "https://github.com/fatedier/frp/releases/download/v${FRP_VERSION}/frp_${FRP_VERSION}_linux_${ARCH}.tar.gz" \
    | tar -xz -C /usr/local/bin --strip-components=1 "frp_${FRP_VERSION}_linux_${ARCH}/frpc" \
    && chmod +x /usr/local/bin/frpc

RUN pip install --no-cache-dir \
    fastapi>=0.100.0 \
    uvicorn>=0.23.0 \
    requests>=2.31.0 \
    jinja2>=3.1.0 \
    python-multipart>=0.0.6

COPY app /app
WORKDIR /app

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "1234"]
