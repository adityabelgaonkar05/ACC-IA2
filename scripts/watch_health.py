#!/usr/bin/env python3
"""Read-only reachability evidence during a rollout or load test; Ctrl-C to stop."""
import sys
import time
import httpx
url=sys.argv[1].rstrip('/')
with httpx.Client(timeout=5) as c:
    while True:
        try:
            h=c.get(url+'/__cloudrunner/health');v=c.get(url+'/__cloudrunner/version')
            print(time.strftime('%H:%M:%S'),h.status_code,v.json(),flush=True)
        except httpx.HTTPError as e: print(time.strftime('%H:%M:%S'),'FAILED',str(e),flush=True)
        time.sleep(2)
