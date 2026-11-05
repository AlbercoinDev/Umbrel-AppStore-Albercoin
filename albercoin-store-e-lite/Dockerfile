FROM python:3.12-slim-bookworm@sha256:935c3c5b12357a7e10caa9c1e6ca5692344d97cf229832ef584be2cec9fc03df

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

EXPOSE 3000 55011 55022

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python3 -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:3000/healthz', timeout=3).read()"

CMD ["python3", "-m", "elite.main"]
