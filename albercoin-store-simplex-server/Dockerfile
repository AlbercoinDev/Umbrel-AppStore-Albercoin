FROM ghcr.io/albercoindev/simplex-chat-cli:1.0.4@sha256:3a367038894d8845cdecd885b0a244c4d36618d975d70cec7338383a19cd1eda AS probe
FROM simplexchat/smp-server:v7.0.1@sha256:7d825822839a9d5ee9e9a99563d77a319816c7a8e0b4b7bf4dcebe448897af54
USER root
LABEL org.opencontainers.image.version="0.1.0" org.opencontainers.image.title="SimpleX Server" io.albercoin.build="tor-check-1"
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
