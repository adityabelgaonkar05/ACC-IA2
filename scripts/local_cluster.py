#!/usr/bin/env python3
import argparse
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from dotenv import load_dotenv
from cloudrunner.local import LocalManager, controller_lease

load_dotenv()
p = argparse.ArgumentParser(description="Manage only CloudRunner's labeled local Docker containers")
p.add_argument("action", choices=["start", "stop", "status", "deploy", "rollback"])
p.add_argument("tag", nargs="?")
a = p.parse_args()
m = LocalManager()
if a.action == "start":
    with controller_lease(): m.ensure()
    print("Local runner pool is ready.")
elif a.action == "stop":
    with controller_lease(): m.stop()
elif a.action == "status":
    import json
    print(json.dumps(m.status(), indent=2))
else:
    # Releases must be serialized by the running management API; CLI never writes concurrently.
    import httpx
    if not a.tag:
        p.error("Provide a release version")
    with httpx.Client(base_url="http://127.0.0.1:8001", timeout=15, trust_env=False) as client:
        session = client.get("/api/session"); session.raise_for_status()
        response = client.post("/api/" + a.action, json={"tag": a.tag},
                               headers={"X-CloudRunner-Token": session.json()["token"]})
        response.raise_for_status()
        print(response.json())
