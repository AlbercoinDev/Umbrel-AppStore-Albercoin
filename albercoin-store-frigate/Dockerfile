FROM node:22-bookworm-slim

ARG TARGETPLATFORM
ARG FRIGATE_VERSION=1.5.2
ARG FRIGATE_PGP_SIG=E94618334C674B40

RUN apt-get update && \
    apt-get install -y --no-install-recommends ca-certificates gnupg wget && \
    if [ "${TARGETPLATFORM}" = "linux/arm64" ] || [ "$(dpkg --print-architecture)" = "arm64" ]; then ARCH="arm64"; else ARCH="amd64"; fi && \
    wget -q \
      "https://github.com/sparrowwallet/frigate/releases/download/${FRIGATE_VERSION}/frigate_${FRIGATE_VERSION}_${ARCH}.deb" \
      "https://github.com/sparrowwallet/frigate/releases/download/${FRIGATE_VERSION}/frigate-${FRIGATE_VERSION}-manifest.txt" \
      "https://github.com/sparrowwallet/frigate/releases/download/${FRIGATE_VERSION}/frigate-${FRIGATE_VERSION}-manifest.txt.asc" \
      "https://keybase.io/craigraw/pgp_keys.asc" && \
    gpg --import pgp_keys.asc && \
    gpg --status-fd 1 --verify "frigate-${FRIGATE_VERSION}-manifest.txt.asc" | grep -q "GOODSIG ${FRIGATE_PGP_SIG} Craig Raw <craig@sparrowwallet.com>" && \
    sha256sum --check "frigate-${FRIGATE_VERSION}-manifest.txt" --ignore-missing && \
    DEBIAN_FRONTEND=noninteractive apt-get install -y "./frigate_${FRIGATE_VERSION}_${ARCH}.deb" && \
    rm -f frigate_* frigate-* pgp_keys.asc && \
    apt-get purge -y --auto-remove wget gnupg && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/* /tmp/* /var/tmp/*

WORKDIR /opt/umbrel-frigate
COPY app ./app
COPY web ./web

ENV NODE_ENV=production
ENV PORT=2107
ENV FRIGATE_TCP_PORT=57001

EXPOSE 2107 57001
VOLUME ["/data"]

CMD ["node", "app/server.js"]
