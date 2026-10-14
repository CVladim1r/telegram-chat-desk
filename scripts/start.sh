#!/usr/bin/env sh
set -eu
cd "$(dirname "$0")/.."
uv sync --frozen
(cd frontend && npm ci && npm run build)
exec uv run --no-sync python -m app

