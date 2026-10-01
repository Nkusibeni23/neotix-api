FROM python:3.12-slim
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv
WORKDIR /app

# Install dependencies first so this layer is cached when only source code changes.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev
ENV PATH="/app/.venv/bin:$PATH"

COPY . .
EXPOSE 8000
# Our middleware writes one structured line per request, so uvicorn's own access log is off.
CMD ["./scripts/start.sh"]
