FROM debian:bookworm-slim

ARG TARGETARCH
ARG FRIGATE_VERSION=1.5.2

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl tar \
    && BUILD_ARCH="${TARGETARCH:-$(dpkg --print-architecture)}" \
    && case "${BUILD_ARCH}" in \
        amd64) FRIGATE_ARCH="x86_64"; FRIGATE_SHA256="4e0f9b0ba90d07354859b7fa29f14deb5c01bd1b4c3449e657e7c0b4dddf667d" ;; \
        arm64) FRIGATE_ARCH="aarch64"; FRIGATE_SHA256="dc7aa026f78f0365d08d04358562a19d961bd7cbdfd862f2ec4a0fb5f3fe0413" ;; \
        *) echo "Unsupported architecture: ${BUILD_ARCH}" >&2; exit 1 ;; \
    esac \
    && curl -fsSL -o /tmp/frigate.tar.gz "https://github.com/sparrowwallet/frigate/releases/download/${FRIGATE_VERSION}/frigate-${FRIGATE_VERSION}-${FRIGATE_ARCH}.tar.gz" \
    && echo "${FRIGATE_SHA256}  /tmp/frigate.tar.gz" | sha256sum -c - \
    && mkdir -p /opt/frigate \
    && tar -xzf /tmp/frigate.tar.gz -C /opt/frigate --strip-components=1 \
    && rm -f /tmp/frigate.tar.gz \
    && apt-get purge -y --auto-remove curl \
    && rm -rf /var/lib/apt/lists/*

COPY start-frigate.sh /usr/local/bin/start-frigate.sh
RUN chmod +x /usr/local/bin/start-frigate.sh

EXPOSE 50005

ENTRYPOINT ["/usr/local/bin/start-frigate.sh"]
