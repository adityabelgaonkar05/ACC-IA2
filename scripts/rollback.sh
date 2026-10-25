#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
exec .venv/bin/python scripts/local_cluster.py rollback "${1:?Usage: ./scripts/rollback.sh v1}"
