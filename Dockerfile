FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim

WORKDIR /app
ENV UV_LINK_MODE=copy
ENV PYTHONUNBUFFERED=1

COPY pyproject.toml README.md .python-version ./
COPY src ./src
COPY docs ./docs

RUN uv sync --no-dev

EXPOSE 8000

CMD ["uv", "run", "uvicorn", "llm_router.main:app", "--host", "0.0.0.0", "--port", "8000"]
