FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim AS base

RUN --mount=type=cache,id=llm_router_apt,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,id=llm_router_apt_lib,target=/var/lib/apt,sharing=locked \
    sed -i 's/deb.debian.org/mirrors.aliyun.com/g' /etc/apt/sources.list.d/debian.sources && \
    apt-get update && apt-get install -y --no-install-recommends \
    less \
    telnet \
    net-tools \
    curl \
    iputils-ping \
    procps

WORKDIR /app
ENV UV_LINK_MODE=copy
ENV UV_HTTP_TIMEOUT=120
ENV PYTHONUNBUFFERED=1


FROM base AS packages

COPY pyproject.toml uv.lock README.md ./
RUN --mount=type=cache,id=llm_router_uv,target=/root/.cache/uv,sharing=locked \
    uv sync --frozen --no-dev --no-install-project


FROM base AS production

ENV VIRTUAL_ENV=/app/.venv
COPY --from=packages ${VIRTUAL_ENV} ${VIRTUAL_ENV}
ENV PATH="${VIRTUAL_ENV}/bin:${PATH}"

COPY src ./src
COPY docs ./docs

ENV PYTHONPATH=/app/src

COPY docker/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

EXPOSE 8000
ENV TZ=UTC

ENTRYPOINT ["/entrypoint.sh"]
