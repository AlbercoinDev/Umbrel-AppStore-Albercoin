FROM ghcr.io/albercoindev/simplex-chat-cli:1.0.8@sha256:4cd4bdbbd31ab222cd83d67855ec532b800f21f63b62f4c450234b1812880b71 AS probe
FROM simplexchat/smp-server:v7.1.0@sha256:d84f2cca92fc0ce68028cd9a9f67c3176d06e35f7c3a2a3f2911be9428f82434
USER root
LABEL org.opencontainers.image.version="0.1.0" org.opencontainers.image.title="SimpleX Server" io.albercoin.build="tor-check-3"
RUN apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends python3 python3-flask python3-waitress python3-qrcode python3-pil tor gosu libgmp10 libtinfo6 libatomic1 libgomp1 libstdc++6 ca-certificates && rm -rf /var/lib/apt/lists/*
COPY --from=probe /usr/local/bin/simplex-chat /usr/local/bin/simplex-chat
COPY --from=probe /usr/share/doc/simplex-chat /usr/share/doc/simplex-chat
COPY src /app
COPY entrypoint.sh /app/entrypoint.sh
RUN chmod 755 /app/entrypoint.sh
ENV DATA_DIR=/data PYTHONUNBUFFERED=1
EXPOSE 2193 5223
STOPSIGNAL SIGINT
ENTRYPOINT ["/app/entrypoint.sh"]
