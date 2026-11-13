FROM python:3.12-slim AS build
WORKDIR /src
COPY backend/pyproject.toml backend/requirements.txt ./
COPY backend/wda ./wda
# Feste Versionen aus requirements.txt, damit jeder Bau dasselbe Image ergibt
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt \
    && pip install --no-cache-dir --prefix=/install --no-deps .

FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 WDA_DATA_DIR=/data
COPY --from=build /install /usr/local
RUN useradd --uid 10001 --create-home --shell /usr/sbin/nologin wda \
    && mkdir /data && chown wda:wda /data
USER wda
WORKDIR /home/wda
VOLUME /data
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD ["python", "-c", "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8080/api/health', timeout=4).status == 200 else 1)"]
CMD ["python", "-m", "wda"]
