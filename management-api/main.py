import asyncio
import os
import secrets
import time

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field, field_validator
from cloudrunner.local import LocalManager, controller_lease
from contextlib import asynccontextmanager
import contextlib

load_dotenv()
MODE=os.getenv("CLOUDRUNNER_MODE","local")
if MODE not in {"mock","local"}: raise RuntimeError("CLOUDRUNNER_MODE must be local or mock")
ORIGIN=os.getenv("FRONTEND_ORIGIN","http://localhost:5173")
URL=os.getenv("RUNNER_URL","http://127.0.0.1:8000").rstrip("/")
KEY=os.getenv("RUNNER_API_KEY","")
@asynccontextmanager
async def lifespan(app):
    lease=contextlib.ExitStack()
    if MODE=="local": lease.enter_context(controller_lease())
    async def watch():
        first=True
        while True:
            try:
                if first:
                    await asyncio.to_thread(local().ensure)
                    first=False
                await asyncio.to_thread(local().monitor)
            except Exception as exc:
                with local().lock:
                    local().state["monitor_error"]=str(exc)
                    local().save()
            await asyncio.sleep(local().sample_interval)
    task=asyncio.create_task(watch()) if MODE=="local" else None
    try:
        yield
    finally:
        cancel.set()
        if task:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError): await task
        if load_task and not load_task.done():
            with contextlib.suppress(asyncio.TimeoutError): await asyncio.wait_for(load_task,16)
        lease.close()

app=FastAPI(title="CloudRunner local management",lifespan=lifespan)
app.add_middleware(CORSMiddleware,allow_origins=[ORIGIN],allow_methods=["GET","POST"],allow_headers=["Content-Type","X-CloudRunner-Token"])
TOKEN=secrets.token_urlsafe(32)
manager=None

def local():
    global manager
    if MODE != "local": raise HTTPException(409,"Action unavailable in UI mock mode; select local mode")
    if manager is None: manager=LocalManager()
    return manager

@app.middleware("http")
async def local_only(request: Request, call_next):
    from starlette.responses import JSONResponse
    if request.client.host not in {"127.0.0.1","::1","testclient"}:
        return JSONResponse({"detail":"Localhost only"},403)
    if request.headers.get("host","").split(":")[0] not in {"localhost","127.0.0.1","testserver","["}:
        return JSONResponse({"detail":"Invalid Host"},403)
    origin=request.headers.get("origin")
    if origin and origin != ORIGIN: return JSONResponse({"detail":"Origin rejected"},403)
    if request.method == "POST" and not secrets.compare_digest(request.headers.get("x-cloudrunner-token",""),TOKEN):
        return JSONResponse({"detail":"Missing local session token"},403)
    if request.method == "POST":
        body=bytearray()
        async for chunk in request.stream():
            body.extend(chunk)
            if len(body)>262144: return JSONResponse({"detail":"Maximum request size is 256 KiB"},413)
        request._body=bytes(body)
    try: return await call_next(request)
    except (RuntimeError,ValueError,KeyError) as e:
        return JSONResponse({"detail":str(e)},502)

@app.get("/api/session")
def session(): return {"token":TOKEN,"mode":MODE}

async def probe():
    try:
        async with httpx.AsyncClient(timeout=5,trust_env=False) as c:
            h=await c.get(URL+"/__cloudrunner/health"); v=await c.get(URL+"/__cloudrunner/version")
            h.raise_for_status(); v.raise_for_status()
            return {"healthy":h.json().get("status")=="ok",**v.json()}
    except (httpx.HTTPError,ValueError): return {"healthy":False,"version":"unavailable"}

@app.get("/api/status")
async def status():
    health=await probe()
    if MODE=="mock":
        return {"mode":MODE,"endpoint":URL,"health":health,"group":None,"refreshes":[],"current_image":None,"message":"UI development mode · Docker data unavailable"}
    data=local().status()
    instances=data["group"]["Instances"]
    latest=data["refreshes"][0] if data["refreshes"] else None
    data["verified"]=bool(instances and all(i["HealthStatus"]=="Healthy" for i in instances)
        and health["healthy"] and health["version"]==data["current_tag"]
        and (not latest or latest["Status"]=="Successful"))
    return {**data,"health":health}

@app.get("/api/releases")
async def releases():
    if MODE=="mock": return []
    return await asyncio.to_thread(local().releases)

class Application(BaseModel):
    name:str=Field(min_length=1,max_length=60)
    path:str=Field(min_length=1,max_length=1024)
    command:str=Field(min_length=1,max_length=2048)
    runtime:str=Field(default="python",pattern="^(python|node|dockerfile)$")
    port:int=Field(default=8080,ge=1024,le=65535)
    health_path:str=Field(default="/health",max_length=200)
    load_path:str=Field(default="/work",max_length=200)
    version:str=Field(pattern=r"^[a-z0-9][a-z0-9_.-]{0,63}$")
    cpu_limit:float=Field(default=1,ge=.25,le=2)
    memory_mb:int=Field(default=512,ge=128,le=2048)

@app.post("/api/application")
async def application(spec:Application):
    if load_task and not load_task.done(): raise HTTPException(409,"Cancel the load test before replacing the API")
    return await asyncio.to_thread(local().build_application,spec.model_dump())


class Release(BaseModel):
    tag: str = Field(pattern=r"^[a-z0-9][a-z0-9_.-]{0,63}$")
    @field_validator("tag")
    @classmethod
    def immutable(cls, value):
        if value == "latest": raise ValueError("Use a versioned release")
        return value

@app.post("/api/deploy")
async def deploy(r:Release):
    if load_task and not load_task.done(): raise HTTPException(409,"Cancel the load test before deploying")
    return await asyncio.to_thread(local().deploy,r.tag)
@app.post("/api/rollback")
async def rollback(r:Release):
    if load_task and not load_task.done(): raise HTTPException(409,"Cancel the load test before rollback")
    return await asyncio.to_thread(local().deploy,r.tag,True)
@app.get("/api/progress")
async def progress(): return [] if MODE=="mock" else local().status()["refreshes"]
@app.get("/api/metrics")
def metrics():
    if MODE=="mock": return {"cpu":[],"instances":[]}
    with local().lock:
        return {"cpu":list(local().state["cpu"]),"instances":list(local().state["counts"])}
@app.get("/api/activities")
def activities():
    return [] if MODE=="mock" else list(local().state["activities"])
@app.get("/api/policies")
def policies():
    if MODE=="mock": return []
    return [{"TargetTrackingConfiguration":{"TargetValue":local().target},
             "description":"CPU / assigned CPU quota: 3 samples >=70% scale out; 12 samples <25% scale in; 6s scale-out / 45s scale-in cooldown; 1–5 replicas"}]

class Program(BaseModel):
    code:str=Field(min_length=1,max_length=16384)
    stdin:str=Field(default="",max_length=16384)
    language:str="python"
@app.post("/api/execute")
async def execute(p:Program):
    try:
        async with httpx.AsyncClient(timeout=15,trust_env=False) as c:
            if MODE=="local" and local().spec()["kind"]!="runner":
                raise HTTPException(409,"The playground is available only for the code runner example")
            response=await c.post(URL+"/execute",json=p.model_dump(),headers={"X-API-Key":KEY})
            response.raise_for_status(); return response.json()
    except httpx.HTTPStatusError as exc:
        try:
            detail = exc.response.json().get("detail", "Runner rejected execution")
        except ValueError:
            detail = "Runner rejected execution"
        raise HTTPException(exc.response.status_code, str(detail))
    except httpx.HTTPError:
        raise HTTPException(502, "Cannot reach the local runner gateway; check .cloudrunner/logs/gateway.log")

class Load(BaseModel):
    duration:int=Field(default=300,ge=30,le=600)
    concurrency:int=Field(default=4,ge=1,le=8)
load={"status":"idle","completed":0,"errors":0,"elapsed":0,"duration":0}
load_task=None
cancel=asyncio.Event()
async def run_load(spec):
    start=time.monotonic()
    load.update(status="running",completed=0,errors=0,elapsed=0,duration=spec.duration)
    code="import time\nend=time.monotonic()+4\nx=0\nwhile time.monotonic()<end: x=(x*1664525+1013904223)%4294967296\nprint(x)"
    selected=local().spec()
    async with httpx.AsyncClient(timeout=15,trust_env=False) as c:
        async def worker():
            while time.monotonic()-start<spec.duration and not cancel.is_set():
                try:
                    if selected["kind"]=="runner":
                        r=await c.post(URL+"/execute",json={"code":code},headers={"X-API-Key":KEY})
                        r.raise_for_status()
                        result=r.json()
                        if result.get("timed_out") or result.get("exit_code")!=0: raise httpx.HTTPError("Program failed")
                    else:
                        r=await c.get(URL+selected["load_path"])
                        r.raise_for_status()
                    load["completed"]+=1
                except (httpx.HTTPError,ValueError): load["errors"]+=1
                load["elapsed"]=round(time.monotonic()-start)
                await asyncio.sleep(0.15)
        await asyncio.gather(*(worker() for _ in range(spec.concurrency)))
    load["status"]="cancelled" if cancel.is_set() else "completed"
@app.post("/api/load")
async def start_load(spec:Load):
    global load_task
    local()  # Real bounded requests only; mock mode cannot run load.
    if load_task and not load_task.done(): raise HTTPException(409,"Load test already running")
    cancel.clear(); load_task=asyncio.create_task(run_load(spec)); return {"status":"starting"}
@app.post("/api/load/cancel")
def stop_load(): cancel.set(); return {"status":"cancelling"}
@app.get("/api/load")
def get_load(): return load
