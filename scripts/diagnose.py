#!/usr/bin/env python3
"""Read-only diagnostics. Never prints .env or container environment variables."""
import json
from pathlib import Path
import subprocess
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from cloudrunner.local import read_state

for node in read_state().get('instances',[]):
    print(f"\nRunner {node['name']} · release {node['tag']} · localhost:{node['port']}",flush=True)
    subprocess.run(['docker','logs','--tail','80',node['id']],check=False)
