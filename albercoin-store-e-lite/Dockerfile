FROM python:3.15-slim@sha256:65a65fc7b30b06cb6df4930fd4650697cb23f4c2698ab81fc87e4b031bc36928

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    HOME=/data

WORKDIR /app

RUN groupadd --gid 1000 app \
    && useradd --uid 1000 --gid 1000 --no-create-home --home-dir /data --shell /usr/sbin/nologin app \
    && mkdir -p /data \
    && chown 1000:1000 /data

COPY src/ /app/
COPY icon.svg /app/icon.svg

USER 1000:1000

EXPOSE 3000 55011 55022 55111 55122 55211 55222

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python3 -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:3000/healthz', timeout=3).read()"

CMD ["python3", "-m", "elite.main"]
