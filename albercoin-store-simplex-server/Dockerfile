FROM simplexchat/smp-server:v7.0.1@sha256:7d825822839a9d5ee9e9a99563d77a319816c7a8e0b4b7bf4dcebe448897af54
USER root
RUN apt-get update && apt-get install -y --no-install-recommends python3 python3-flask python3-waitress python3-qrcode python3-pil tor gosu && rm -rf /var/lib/apt/lists/*
COPY src /app
COPY entrypoint.sh /app/entrypoint.sh
RUN chmod 755 /app/entrypoint.sh
ENV DATA_DIR=/data PYTHONUNBUFFERED=1
EXPOSE 2193 5223
STOPSIGNAL SIGINT
ENTRYPOINT ["/app/entrypoint.sh"]
