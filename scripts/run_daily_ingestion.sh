#!/bin/sh
set -eu

PROJECT_ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$PROJECT_ROOT"
exec "$PROJECT_ROOT/.venv/bin/python" -m scripts.ingest_stocks --refresh-loaded
