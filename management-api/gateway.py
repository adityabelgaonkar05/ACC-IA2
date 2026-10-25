"""Loopback-only round-robin gateway; routes only to readiness-approved local runners."""
import itertools
import asyncio
import time
import json
from pathlib import Path
from cloudrunner.local import state_path
from contextlib import asynccontextmanager

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, Request, HTTPException
from starlette.responses import Response, JSONResponse
from cloudrunner.local import read_state

load_dotenv()
sequence = itertools.count()
traffic={}

def flush_traffic():
    path=state_path().with_name("traffic.json")
    path.parent.mkdir(parents=True,exist_ok=True)
    temporary=path.with_suffix(".tmp")
    temporary.write_text(json.dumps(traffic))
    temporary.replace(path)



@asynccontextmanager
async def lifespan(app):
    async with httpx.AsyncClient(timeout=15, trust_env=False) as client:
        app.state.client = client
        traffic.clear()
        flush_traffic()
        yield


app = FastAPI(title="CloudRunner local gateway", lifespan=lifespan)


@app.middleware("http")
async def loopback(request: Request, call_next):
    if request.client.host not in {"127.0.0.1", "::1", "testclient"}:
        return JSONResponse({"detail": "Localhost only"}, 403)
    if request.headers.get("host", "").split(":")[0] not in {"localhost", "127.0.0.1", "testserver", "["}:
        return JSONResponse({"detail": "Invalid Host"}, 403)
    return await call_next(request)


def targets():
    # State is written atomically by the single controller. Never accept a target URL from a request.
    return [n for n in read_state().get("instances", [])
            if n.get("healthy") and isinstance(n.get("port"), int) and 1024 <= n["port"] <= 65535]


@app.get("/__cloudrunner/health")
@app.get("/health")
async def health(request: Request):
    nodes=targets()
    if request.url.path=="/health" and nodes and nodes[0].get("kind")=="api":
        return await forward(request,"health")
    for node in nodes:
        try:
            r = await request.app.state.client.get(f"http://127.0.0.1:{node['port']}"+node.get("health_path","/health"))
            if 200 <= r.status_code < 300:
                return {"status": "ok", "provider": "local-docker"}
        except httpx.HTTPError:
            continue
    raise HTTPException(503, "No ready local runners")


async def forward(request, path):
    nodes = targets()
    if not nodes:
        raise HTTPException(503, "No ready local runners; start ./scripts/local.sh")
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > 262144:
            raise HTTPException(413, "Maximum request size is 256 KiB")
    node = nodes[next(sequence) % len(nodes)]
    counter=traffic.setdefault(node["name"],{"active":0,"requests":0,"errors":0,"durations_ms":[]})
    counter["active"]+=1
    flush_traffic()
    started=time.monotonic()
    failed=False
    try:
        query=("?"+request.url.query) if request.url.query else ""
        response=await asyncio.wait_for(request.app.state.client.request(
            request.method,f"http://127.0.0.1:{node['port']}/{path}"+query,
            content=bytes(body),headers={k:v for k,v in request.headers.items()
                if k.lower() not in {"host","connection","content-length","transfer-encoding"}}),timeout=15)
        failed=response.status_code>=500
        return Response(response.content,status_code=response.status_code,
            headers={k:v for k,v in response.headers.items()
                if k.lower() not in {"connection","content-length","transfer-encoding","content-encoding"}})
    except (httpx.HTTPError,TimeoutError):
        failed=True
        raise HTTPException(502,"Runner connection failed; execution was not automatically retried")
    finally:
        counter["active"]-=1
        counter["requests"]+=1
        counter["errors"]+=int(failed)
        counter["durations_ms"].append(round((time.monotonic()-started)*1000,1))
        counter["durations_ms"]=counter["durations_ms"][-100:]
        flush_traffic()


@app.get("/__cloudrunner/version")
@app.get("/version")
async def version(request: Request):
    nodes=targets()
    if request.url.path=="/version" and nodes and nodes[0].get("kind")=="api":
        return await forward(request,"version")
    if not nodes: raise HTTPException(503,"No ready local replicas")
    node=nodes[next(sequence)%len(nodes)]
    return {"version":node["tag"],"instance_id":node["name"]}

@app.api_route("/{path:path}",methods=["GET","POST","PUT","PATCH","DELETE","HEAD","OPTIONS"])
async def proxy(request: Request,path:str):
    return await forward(request,path)
