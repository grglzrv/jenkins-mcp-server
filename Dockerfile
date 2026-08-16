# syntax=docker/dockerfile:1.7
ARG PYTHON_IMAGE=python:3.12-slim-bookworm@sha256:a116514e19457bcb7af7efe9c3dd0b9b71e85b317694e7882a1c52aa15a78134

FROM ${PYTHON_IMAGE} AS builder
ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1
WORKDIR /build
COPY pyproject.toml README.md LICENSE ./
COPY requirements ./requirements
COPY src ./src
RUN python -m pip install --require-hashes --only-binary=:all: -r requirements/build.txt \
    && python -m pip download --require-hashes --only-binary=:all: \
      --dest=/wheels -r requirements/runtime.txt \
    && python -m pip wheel --no-build-isolation --no-deps --wheel-dir=/wheels .

FROM ${PYTHON_IMAGE} AS runtime
ARG APP_VERSION=dev
ARG APP_UID=10001
ARG APP_GID=10001
LABEL org.opencontainers.image.title="Jenkins MCP Server" \
      org.opencontainers.image.description="Production-ready Jenkins MCP server for Hermes Agent" \
      org.opencontainers.image.source="https://github.com/grglzrv/jenkins-mcp-server" \
      org.opencontainers.image.documentation="https://github.com/grglzrv/jenkins-mcp-server" \
      org.opencontainers.image.licenses="MIT" \
      org.opencontainers.image.version="${APP_VERSION}"

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    MCP_TRANSPORT=streamable-http \
    MCP_HOST=0.0.0.0 \
    MCP_PORT=8000 \
    MCP_PATH=/mcp \
    MCP_HEALTH_HOST=0.0.0.0 \
    MCP_HEALTH_PORT=8081

RUN apt-get update \
    && apt-get install --yes --no-install-recommends ca-certificates tini \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid "${APP_GID}" app \
    && useradd --uid "${APP_UID}" --gid "${APP_GID}" --create-home --shell /usr/sbin/nologin app \
    && mkdir -p /data /certs \
    && chown -R app:app /data /certs

COPY --from=builder /wheels /wheels
RUN python -m pip install --no-index --find-links=/wheels /wheels/jenkins_mcp_server-*.whl \
    && rm -rf /wheels

USER app:app
WORKDIR /home/app
EXPOSE 8000 8081
VOLUME ["/data"]
HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
  CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8081/readyz', timeout=2).read()"]
ENTRYPOINT ["/usr/bin/tini", "--", "jenkins-mcp-server"]
CMD ["--transport", "streamable-http"]
