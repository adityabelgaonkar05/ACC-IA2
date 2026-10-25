#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
for tool in python3 npm docker; do
  command -v "$tool" >/dev/null || { echo "Install $tool first." >&2; exit 1; }
done
python3 -c 'import sys; assert sys.version_info >= (3,12), "Python 3.12 or newer is required"'
node -e 'if(Number(process.versions.node.split(".")[0])<22) throw Error("Node 22 or newer is required")'
docker info >/dev/null 2>&1 || { echo 'Start Docker Desktop / Docker Engine, then run this script again.' >&2; exit 1; }
python3 - <<'PY'
import socket
for port in (8000, 8001, 5173):
    with socket.socket() as s:
        try: s.bind(('127.0.0.1', port))
        except OSError: raise SystemExit(f'Port {port} is busy. Stop the old app first (including docker compose down if used).')
PY
if [[ ! -x .venv/bin/python ]]; then python3 -m venv .venv; fi
.venv/bin/python -m pip install -r requirements.txt
(cd dashboard && npm ci)
.venv/bin/python - <<'PY'
from pathlib import Path
import secrets
from dotenv import dotenv_values, set_key
p=Path('.env')
if not p.exists(): p.write_text(Path('.env.example').read_text())
values=dotenv_values(p)
if not values.get('RUNNER_API_KEY') or values['RUNNER_API_KEY']=='replace-with-a-long-random-value':
    set_key(p,'RUNNER_API_KEY',secrets.token_urlsafe(32))
set_key(p,'CLOUDRUNNER_MODE','local')
set_key(p,'RUNNER_URL','http://127.0.0.1:8000')
set_key(p,'FRONTEND_ORIGIN','http://localhost:5173')
p.chmod(0o600)
PY
for tag in v1 v2; do
  if ! docker image inspect "cloudrunner-runner:$tag" >/dev/null 2>&1; then ./scripts/build-release.sh "$tag"; fi
done
mkdir -p .cloudrunner/logs
.venv/bin/python scripts/local_cluster.py start
management_pid=''
gateway_pid=''
frontend_pid=''
cleanup() {
  trap - EXIT
  set +e
  for pid in "$frontend_pid" "$management_pid" "$gateway_pid"; do
    if [[ -n "$pid" ]]; then kill "$pid" 2>/dev/null; fi
  done
  for pid in "$frontend_pid" "$management_pid" "$gateway_pid"; do
    if [[ -n "$pid" ]]; then wait "$pid" 2>/dev/null; fi
  done
  .venv/bin/python scripts/local_cluster.py stop
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
.venv/bin/python -m uvicorn main:app --app-dir management-api --host 127.0.0.1 --port 8001 > .cloudrunner/logs/management.log 2>&1 &
management_pid=$!
.venv/bin/python -m uvicorn gateway:app --app-dir management-api --host 127.0.0.1 --port 8000 > .cloudrunner/logs/gateway.log 2>&1 &
gateway_pid=$!
npm --prefix dashboard run dev > .cloudrunner/logs/dashboard.log 2>&1 &
frontend_pid=$!
.venv/bin/python - <<'PY'
import httpx,time
with httpx.Client(timeout=2,trust_env=False) as c:
    for _ in range(30):
        try:
            if c.get('http://127.0.0.1:8000/__cloudrunner/health').status_code==200 and c.get('http://127.0.0.1:8001/api/session').status_code==200 and c.get('http://localhost:5173').status_code==200:
                break
        except httpx.HTTPError: pass
        time.sleep(1)
    else: raise SystemExit('Startup failed. Inspect .cloudrunner/logs/.')
PY
printf '\nCloudRunner is ready: http://localhost:5173\nAPI gateway: http://127.0.0.1:8000\nNo AWS account or credentials needed. Press Ctrl+C to stop.\n\n'
while kill -0 "$management_pid" 2>/dev/null && kill -0 "$gateway_pid" 2>/dev/null && kill -0 "$frontend_pid" 2>/dev/null; do sleep 2; done
echo 'A service stopped. Inspect .cloudrunner/logs/.' >&2
exit 1
