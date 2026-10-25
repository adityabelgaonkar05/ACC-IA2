#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
# Stop ./scripts/local.sh first. Only this project's labeled containers are removed.
exec .venv/bin/python scripts/local_cluster.py stop
