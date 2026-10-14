FROM node:22.19.0-bookworm-slim AS frontend
WORKDIR /build
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build


FROM python:3.12-slim-bookworm AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    CHAT_HOST=0.0.0.0 \
    CHAT_DATABASE_URL=sqlite+aiosqlite:////app/data/chat.db
WORKDIR /app
RUN pip install --no-cache-dir uv==0.9.27 && useradd --uid 10001 --create-home app

COPY pyproject.toml uv.lock ./
COPY backend/ ./backend/
RUN uv sync --frozen --no-dev

COPY --from=frontend /build/dist ./frontend/dist
RUN mkdir -p /app/data && chown -R app:app /app
USER app
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s CMD ["/app/.venv/bin/python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=3)"]
CMD ["/app/.venv/bin/python", "-m", "app"]
