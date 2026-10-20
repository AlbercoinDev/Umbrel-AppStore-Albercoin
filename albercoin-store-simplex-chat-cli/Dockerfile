FROM ubuntu:24.04
ARG TARGETARCH
ARG SIMPLEX_VERSION=v7.0.3
LABEL org.opencontainers.image.title="SimpleX Chat CLI for Umbrel" \
      org.opencontainers.image.version="1.0.2" \
      org.opencontainers.image.source="https://github.com/AlbercoinDev/Umbrel-AppStore-Albercoin" \
      org.opencontainers.image.licenses="AGPL-3.0-only"
ENV DEBIAN_FRONTEND=noninteractive PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates curl python3 python3-venv libgmp10 libnuma1 libssl3t64 zlib1g libtinfo6 libatomic1 libgomp1 libstdc++6 gosu \
    && rm -rf /var/lib/apt/lists/*
RUN set -eu; \
    case "$TARGETARCH" in \
      amd64) arch=x86_64; hash=895fb14cfaa662d1c0947f2f871141fc89680fe3e70bff64c366cbe9e59aa4f0 ;; \
      arm64) arch=aarch64; hash=2d2e62351f11bc51ae659618584722b38ea5a6796b806c9a388ee6e3124c90ac ;; \
      *) echo "Unsupported architecture: $TARGETARCH" >&2; exit 1 ;; \
    esac; \
    test "$SIMPLEX_VERSION" = v7.0.3; \
    curl --fail --location --retry 3 "https://github.com/simplex-chat/simplex-chat/releases/download/${SIMPLEX_VERSION}/simplex-chat-ubuntu-24_04-${arch}" -o /usr/local/bin/simplex-chat; \
    echo "$hash  /usr/local/bin/simplex-chat" | sha256sum -c -; \
    chmod 755 /usr/local/bin/simplex-chat; \
    mkdir -p /usr/share/doc/simplex-chat; \
    curl --fail --location --retry 3 "https://github.com/simplex-chat/simplex-chat/releases/download/${SIMPLEX_VERSION}/LICENSE" -o /usr/share/doc/simplex-chat/LICENSE
RUN existing="$(getent passwd 1000 | cut -d: -f1)"; \
    if [ -n "$existing" ]; then userdel -r "$existing" 2>/dev/null || userdel "$existing"; fi; \
    useradd --uid 1000 --create-home simplex && python3 -m venv /opt/venv
COPY requirements.txt /app/requirements.txt
RUN /opt/venv/bin/pip install --no-cache-dir -r /app/requirements.txt
COPY src /app/src
COPY entrypoint.sh /app/entrypoint.sh
RUN chmod 755 /app/entrypoint.sh
ENV HOME=/data/simplex APP_DATA=/data
WORKDIR /app
EXPOSE 2191 2192
ENTRYPOINT ["/bin/sh", "/app/entrypoint.sh"]
