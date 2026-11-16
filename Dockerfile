FROM node:22-slim AS web
WORKDIR /src/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend ./
# der Bau schreibt nach ../backend/wda/static
RUN npx ng build

FROM python:3.12-slim AS build
WORKDIR /src
COPY backend/pyproject.toml backend/requirements.txt ./
COPY backend/wda ./wda
COPY --from=web /src/backend/wda/static ./wda/static
# Feste Versionen aus requirements.txt, damit jeder Bau dasselbe Image ergibt
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt \
    && pip install --no-cache-dir --prefix=/install --no-deps .

FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 WDA_DATA_DIR=/data
COPY --from=build /install /usr/local
# Schrift und Satz für den PDF-Export
RUN apt-get update \
    && apt-get install -y --no-install-recommends libpango-1.0-0 libpangoft2-1.0-0 \
       libharfbuzz-subset0 fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*
RUN useradd --uid 10001 --create-home --shell /usr/sbin/nologin wda \
    && mkdir /data && chown wda:wda /data
USER wda
WORKDIR /home/wda
VOLUME /data
# Standardport; WDA_PORT wählt einen anderen (Healthcheck und Compose folgen ihm)
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD ["python", "-c", "import os,urllib.request,sys; port=os.environ.get('WDA_PORT') or '8080'; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:' + port + '/api/health', timeout=4).status == 200 else 1)"]
CMD ["python", "-m", "wda"]
