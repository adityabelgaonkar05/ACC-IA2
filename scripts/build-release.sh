#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
tag="${1:?Usage: ./scripts/build-release.sh v3}"
if ! [[ "$tag" =~ ^[a-z0-9][a-z0-9_.-]{0,63}$ ]] || [[ "$tag" == latest ]]; then
  echo 'Use a version such as v1, v2 or v3; latest is not a release.' >&2
  exit 1
fi
pool=$(.venv/bin/python -c 'from dotenv import load_dotenv; import os; load_dotenv(); print(os.getenv("LOCAL_POOL","cloudrunner"))')
if docker image inspect "cloudrunner-runner:$tag" >/dev/null 2>&1; then
  echo "Release $tag already exists. Choose a new version; existing release tags are preserved." >&2
  exit 1
fi
docker build --build-arg "BUILD_VERSION=$tag" --label "cloudrunner.project=$pool" \
  -t "cloudrunner-runner:$tag" runner-api
printf '\nBuilt %s. Open Deployments and click Refresh, then Deploy.\n' "$tag"
