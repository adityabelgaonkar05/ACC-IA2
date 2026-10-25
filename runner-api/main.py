import asyncio
import hmac
import os
import time
import uuid

import logging
from fastapi import FastAPI, Header, HTTPException, Request
from pydantic import BaseModel, Field

app = FastAPI(title="CloudRunner execution API")
KEY = os.getenv("RUNNER_API_KEY", "")
IMAGE = os.getenv("SANDBOX_IMAGE", "cloudrunner-sandbox:local")
@app.middleware("http")
async def bounded_request(request: Request, call_next):
    from starlette.responses import JSONResponse
    if request.method == "POST":
        body = bytearray()
        async for chunk in request.stream():
            body.extend(chunk)
            if len(body) > 262144:
                return JSONResponse({"detail":"Maximum request size is 256 KiB"},413)
        request._body = bytes(body)
    return await call_next(request)

slots = asyncio.Semaphore(int(os.getenv("MAX_CONCURRENT", "4")))
LIMIT = 65536

class Program(BaseModel):
    code: str = Field(min_length=1, max_length=16384)
    stdin: str = Field(default="", max_length=16384)
    language: str = "python"

def execute(program):
    import docker
    client = docker.from_env(timeout=10)
    container = None
    start = time.monotonic()
    timed_out = False
    # A bounded JSON argument reaches only the trusted wrapper. No shell, host
    # mount, or Docker attach socket is involved. The program still receives stdin.
    import json
    payload = json.dumps(program.model_dump(), ensure_ascii=False)
    try:
        container = client.containers.create(
            IMAGE, ["python", "-I", "/sandbox.py", payload], name="cr-job-" + uuid.uuid4().hex,
            user="65534:65534", network_disabled=True, read_only=True,
            cap_drop=["ALL"], security_opt=["no-new-privileges:true"],
            mem_limit="128m", memswap_limit="128m", nano_cpus=500_000_000,
            pids_limit=32, tmpfs={"/tmp": "rw,noexec,nosuid,size=16m,mode=1777"},
            use_config_proxy=False, ulimits=[docker.types.Ulimit(name="fsize", soft=1048576, hard=1048576)],
            log_config=docker.types.LogConfig(type="local", config={"max-size":"1m", "max-file":"1"}),
            labels={"cloudrunner.job":"true", "cloudrunner.role":"job",
                    "cloudrunner.project":os.getenv("POOL_ID","cloudrunner"),
                    "cloudrunner.instance":os.getenv("INSTANCE_ID","local")})
        container.start()
        try:
            result = container.wait(timeout=7)
        except Exception:
            timed_out = True
            container.kill()
            result = {"StatusCode":137}
        import json
        raw = container.logs(stdout=True, stderr=False, tail=10)
        try:
            out = json.loads(raw)
        except (ValueError, TypeError):
            out = {"stdout":"", "stderr":container.logs(stderr=True,stdout=False).decode(errors="replace")[:LIMIT], "exit_code":result["StatusCode"], "timed_out":timed_out}
        out["duration_ms"] = round((time.monotonic()-start)*1000)
        out["timed_out"] = timed_out or out.get("timed_out",False)
        return out
    finally:
        if container is not None:
            container.remove(force=True)
        client.close()

@app.get("/health")
def health():
    client = None
    try:
        import docker
        client = docker.from_env(timeout=2)
        client.ping()
        client.images.get(IMAGE)
    except Exception:
        raise HTTPException(503, "Execution engine not ready")
    finally:
        if client is not None:
            client.close()
    return {"status":"ok"}

@app.get("/version")
def version():
    return {"version":os.getenv("APP_VERSION","local"), "instance_id":os.getenv("INSTANCE_ID","local")}

@app.post("/execute")
async def run(program: Program, x_api_key: str = Header(default="")):
    if not KEY or not hmac.compare_digest(x_api_key, KEY):
        raise HTTPException(401, "Invalid execution key")
    if program.language != "python":
        raise HTTPException(422, "Only Python is supported")
    if len(program.code.encode()) > 16384 or len(program.stdin.encode()) > 16384:
        raise HTTPException(413,"Maximum code/input size is 16 KiB each")
    try:
        await asyncio.wait_for(slots.acquire(), timeout=0.1)
    except TimeoutError:
        raise HTTPException(429,"All execution slots are busy")
    try:
        return await asyncio.to_thread(execute, program)
    except Exception as exc:
        logging.exception("Container execution failed")
        # Local operators need the actionable Docker error, rather than a generic
        # HTTP failure. Docker API explanations contain no runner environment.
        reason = getattr(exc, "explanation", None) or str(exc)
        raise HTTPException(503, "Execution engine failed: " + str(reason)[:1000])
    finally:
        slots.release()
