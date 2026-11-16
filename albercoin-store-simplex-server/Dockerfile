FROM ghcr.io/albercoindev/simplex-chat-cli:1.0.9@sha256:0dfadc14906cd6796e29010874e94e18caea1aeec1227dbbca1a572f7adbec5e AS probe
FROM python:3.14-slim@sha256:9aaa474097d094040649f2162a627c0049b408f52138d980916788d80f04802f
USER root
LABEL org.opencontainers.image.version="0.1.0" org.opencontainers.image.title="SimpleX Server" io.albercoin.build="tor-check-4"
RUN apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends tor gosu libgmp10 libssl3t64 zlib1g libtinfo6 libatomic1 libgomp1 libstdc++6 ca-certificates && rm -rf /var/lib/apt/lists/*
COPY --from=simplexchat/smp-server:v7.1.0@sha256:d84f2cca92fc0ce68028cd9a9f67c3176d06e35f7c3a2a3f2911be9428f82434 /usr/local/bin/smp-server /usr/local/bin/smp-server
RUN pip install --no-cache-dir flask waitress qrcode pillow
COPY --from=probe /usr/local/bin/simplex-chat /usr/local/bin/simplex-chat
COPY --from=probe /usr/share/doc/simplex-chat /usr/share/doc/simplex-chat
COPY src /app
COPY entrypoint.sh /app/entrypoint.sh
RUN chmod 755 /app/entrypoint.sh
ENV DATA_DIR=/data PYTHONUNBUFFERED=1
EXPOSE 2193 5223
STOPSIGNAL SIGINT
ENTRYPOINT ["/app/entrypoint.sh"]
