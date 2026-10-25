"""Trusted wrapper inside the disposable container; bounded concurrent output drains."""
import json
import os
import subprocess
import sys
import threading

# New runners use an explicit JSON argument. Keep stdin compatibility for the
# standalone sandbox image; submitted programs receive a separate stdin pipe.
request = json.loads(sys.argv[1] if len(sys.argv) == 2 else sys.stdin.buffer.read(262144))
streams = {"stdout": bytearray(), "stderr": bytearray()}
process = subprocess.Popen([sys.executable,"-I","-c",request["code"]],
    stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    cwd="/tmp", env={"PATH":"/usr/local/bin:/usr/bin:/bin","HOME":"/tmp"}, start_new_session=True)
def drain(name, pipe):
    while True:
        chunk = pipe.read(4096)
        if not chunk: break
        remaining = 65536-len(streams[name])
        streams[name].extend(chunk[:max(0,remaining)])
        if remaining < len(chunk):
            try: os.killpg(process.pid, 9)
            except ProcessLookupError: pass
            break
threads = [threading.Thread(target=drain,args=(name,getattr(process,name)),daemon=True) for name in streams]
for t in threads: t.start()
timed_out = False
try:
    process.stdin.write(request.get("stdin", "").encode()); process.stdin.close()
    process.wait(timeout=5)
except BrokenPipeError:
    process.wait(timeout=5)
except subprocess.TimeoutExpired:
    timed_out=True
    os.killpg(process.pid,9); process.wait()
finally:
    # Kill surviving children before joining pipes.
    try: os.killpg(process.pid,9)
    except ProcessLookupError: pass
for t in threads: t.join(timeout=0.5)
print(json.dumps({**{k:v.decode(errors="replace") for k,v in streams.items()},
    "exit_code":process.returncode,"timed_out":timed_out}))
