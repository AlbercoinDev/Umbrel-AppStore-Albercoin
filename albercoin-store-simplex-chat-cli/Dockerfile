FROM python:3.14-slim@sha256:9aaa474097d094040649f2162a627c0049b408f52138d980916788d80f04802f
ARG TARGETARCH
ARG SIMPLEX_VERSION=v7.1.4
LABEL org.opencontainers.image.title="SimpleX Chat CLI for Umbrel" \
       org.opencontainers.image.version="1.0.9" \
      org.opencontainers.image.source="https://github.com/AlbercoinDev/Umbrel-AppStore-Albercoin" \
      org.opencontainers.image.licenses="AGPL-3.0-only"
ENV DEBIAN_FRONTEND=noninteractive PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates curl age tor libgmp10 libnuma1 libssl3t64 zlib1g libtinfo6 libatomic1 libgomp1 libstdc++6 gosu passwd \
    && rm -rf /var/lib/apt/lists/*
RUN set -eu; \
    case "$TARGETARCH" in \
      amd64) arch=x86_64; hash=15a64d3fe75782900b3686aed0f635569b1f3526c47139ff30ac1ce1a50189ca ;; \
      arm64) arch=aarch64; hash=424d9b8c2e46abc221be190475c9d5a71c78b779791f722de1a25325a1b15e5f ;; \
      *) echo "Unsupported architecture: $TARGETARCH" >&2; exit 1 ;; \
    esac; \
    test "$SIMPLEX_VERSION" = v7.1.4; \
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
