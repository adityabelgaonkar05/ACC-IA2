"""Local Docker controller. No AWS credentials, simulated metrics, or host-code execution."""
import contextlib
import fcntl
import copy
import json
import os
from pathlib import Path
import re
import subprocess
import threading
import time
import uuid
import tempfile
from cloudrunner.projects import validate as validate_project, snapshot
from datetime import datetime, timezone

import httpx

ROOT = Path(__file__).resolve().parents[1]
TAG = re.compile(r"^(?!latest$)[a-z0-9][a-z0-9_.-]{0,63}$")


def now():
    return datetime.now(timezone.utc).isoformat()


def docker(*args, timeout=30, env=None):
    try:
        result = subprocess.run(["docker", *args], capture_output=True, text=True,
                                timeout=timeout, env=env)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError(f"Docker unavailable: {exc}") from exc
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or "Docker command failed")
    return result.stdout.strip()


def state_path():
    return ROOT / ".cloudrunner" / "state.json"


def read_state(path=None):
    try:
        return json.loads((path or state_path()).read_text())
    except FileNotFoundError:
        return {}


@contextlib.contextmanager
def controller_lease(path=None):
    lock_path=(path or state_path()).with_suffix(".lock")
    lock_path.parent.mkdir(parents=True,exist_ok=True)
    with lock_path.open("a+") as handle:
        try:
            fcntl.flock(handle,fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError("A local controller is already running; stop it before starting or cleaning up")
        try:
            yield
        finally:
            fcntl.flock(handle,fcntl.LOCK_UN)


class LocalManager:
    def __init__(self, engine=docker, path=None):
        self.engine = engine
        self.path = path or state_path()
        self.pool = os.getenv("LOCAL_POOL", "cloudrunner")
        if not re.fullmatch(r"[a-z][a-z0-9-]{0,31}", self.pool):
            raise ValueError("Invalid LOCAL_POOL")
        self.repository = "cloudrunner-runner"
        self.minimum = 1
        self.maximum = 5
        self.target = 70
        # Fast local demonstration policy; scale-in stays conservative.
        self.scale_out_cooldown = 6
        self.scale_in_cooldown = 45
        self.sample_interval = 1
        self.key = os.getenv("RUNNER_API_KEY", "")
        self.lock = threading.RLock()
        self.operation = threading.Lock()
        self.high = self.low = 0
        self.last_scale = 0.0
        self.state = read_state(self.path) or {
            "instances": [], "desired": 1, "current_tag": None,
            "current_image": None, "known_good": {}, "refreshes": [],
            "activities": [], "cpu": [], "counts": [], "monitor_error": None,
        }

    def spec(self, image=None):
        return self.state.get("release_specs", {}).get(image or self.state["current_image"], {
            "kind":"runner", "name":"Code runner example", "port":8000, "health_path":"/health",
            "cpu_limit":1.0, "memory_mb":256, "load_path":"/execute", "runtime":"python"})

    def build_application(self, spec):
        spec=validate_project(spec)
        if not TAG.fullmatch(spec["version"]): raise ValueError("Use an immutable version tag, not latest")
        if not self.operation.acquire(blocking=False): raise ValueError("A build, deploy or scale operation is active")
        self.state["build"]={"status":"Building", "version":spec["version"], "message":"Preparing filtered Docker build context"}
        self.save()
        threading.Thread(target=self._build_application,args=(spec,),daemon=True).start()
        return {"status":"Building", "tag":spec["version"]}

    def _build_application(self, spec):
        try:
            if any(r["tag"]==spec["version"] for r in self.releases()):
                raise ValueError("This release tag already exists; choose a new version")
            with tempfile.TemporaryDirectory(prefix="cloudrunner-build-") as temporary:
                context=Path(temporary)/"source"
                filename=snapshot(spec,context)
                self.engine("build", "--label", f"cloudrunner.project={self.pool}",
                    "--label", f"cloudrunner.version={spec['version']}", "-f", str(context/filename),
                    "-t", f"{self.repository}:{spec['version']}", str(context), timeout=300)
            image=self.image(spec["version"])
            with self.lock:
                self.state.setdefault("release_specs",{})[image]=spec
                rollout={"InstanceRefreshId":uuid.uuid4().hex,"Status":"Pending","PercentageComplete":0,
                    "StatusReason":"Build completed; waiting for healthy API replicas","StartTime":now(),
                    "tag":spec["version"],"image":image}
                self.state["refreshes"].insert(0,rollout)
                self.state["refreshes"]=self.state["refreshes"][:50]
                self.state["build"].update(status="Deploying",message="Image built; checking replacement readiness")
                self.save()
            self._rollout(rollout,spec["version"],image,release_operation=False)
            self.state["build"].update(status=rollout["Status"],message=rollout["StatusReason"])
            self.save()
        except Exception as exc:
            self.state["build"].update(status="Failed",message=str(exc))
            self.save()
            self.event(f"API build failed: {exc}","Failed")
        finally:
            self.operation.release()

    def save(self):
        # Only the single management process writes while the app is running.
        with self.lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.path.with_suffix(".tmp")
            temporary.write_text(json.dumps(self.state, indent=2))
            temporary.replace(self.path)

    def event(self, description, status="Successful"):
        with self.lock:
            self.state["activities"].insert(0, {
                "ActivityId": uuid.uuid4().hex, "Description": description,
                "StatusCode": status, "Cause": "Local Docker controller", "StartTime": now(),
            })
            self.state["activities"] = self.state["activities"][:100]
            self.save()

    def releases(self):
        raw = self.engine("image", "ls", "--no-trunc", "--filter",
                          f"label=cloudrunner.project={self.pool}", "--format", "{{json .}}")
        result = []
        for line in raw.splitlines():
            image = json.loads(line)
            tag = image.get("Tag", "")
            if image.get("Repository") == self.repository and TAG.fullmatch(tag):
                created=image.get("CreatedAt", "")
                try:
                    created=datetime.strptime(" ".join(created.split()[:3]), "%Y-%m-%d %H:%M:%S %z").isoformat()
                except ValueError:
                    pass
                result.append({"tag": tag, "digest": image["ID"],
                               "pushed_at": created,
                               "known_good": self.state["known_good"].get(tag) == image["ID"]})
        return sorted(result, key=lambda item: item["pushed_at"] or "", reverse=True)

    def image(self, tag):
        if not TAG.fullmatch(tag):
            raise ValueError("Use a version such as v1 or v2; latest is not a release")
        release = next((r for r in self.releases() if r["tag"] == tag), None)
        if not release:
            raise ValueError("Build this project's release first: ./scripts/build-release.sh VERSION")
        return release["digest"]

    def owned(self, identifier):
        data = json.loads(self.engine("inspect", identifier))[0]
        if data.get("Config", {}).get("Labels", {}).get("cloudrunner.project") != self.pool:
            raise ValueError("Refusing to manage a container outside this project")
        return data

    def spawn(self, tag, image):
        spec=self.spec(image)
        if spec["kind"]=="runner" and (not self.key or self.key=="replace-with-a-long-random-value"):
            raise ValueError("Configure a random RUNNER_API_KEY first")
        name=f"{self.pool}-worker-{uuid.uuid4().hex[:8]}"
        env={**os.environ,"RUNNER_API_KEY":self.key}
        args=["run","-d","--name",name,"--restart","unless-stopped",
            "--label",f"cloudrunner.project={self.pool}","--label","cloudrunner.role=worker",
            "--label",f"cloudrunner.version={tag}","-p",f"127.0.0.1::{spec['port']}",
            "--cpus",str(spec["cpu_limit"]),"--memory",f"{spec['memory_mb']}m","--pids-limit","128",
            "-e",f"APP_VERSION={tag}","-e",f"INSTANCE_ID={name}","-e",f"PORT={spec['port']}"]
        if spec["kind"]=="runner":
            args += ["-v","/var/run/docker.sock:/var/run/docker.sock","-e","RUNNER_API_KEY",
                "-e",f"POOL_ID={self.pool}","-e","MAX_CONCURRENT=2","-e",f"SANDBOX_IMAGE={image}"]
        else:
            args += ["--user","10001:10001","--cap-drop","ALL","--security-opt","no-new-privileges:true",
                "--read-only","--tmpfs","/tmp:rw,nosuid,size=64m","-e","HOME=/tmp"]
        if spec["kind"]!="runner": args += ["--entrypoint",spec["argv"][0]]
        args.append(image)
        if spec["kind"]!="runner": args += spec["argv"][1:]
        identifier=self.engine(*args,env=env)
        try:
            data=self.owned(identifier)
            port=int(data["NetworkSettings"]["Ports"][f"{spec['port']}/tcp"][0]["HostPort"])
            node={"id":identifier,"name":name,"port":port,"tag":tag,"image":image,"healthy":False,
                "kind":spec["kind"],"health_path":spec["health_path"],"cpu_limit":spec["cpu_limit"]}
            for _ in range(45):
                if self.healthy(node):
                    node["healthy"]=True
                    return node
                time.sleep(1)
            logs=self.engine("logs","--tail","20",identifier)
            raise RuntimeError("API readiness failed. Bind to 0.0.0.0 and check the configured health path. " + logs[-2000:])
        except Exception:
            self.remove(identifier)
            raise

    @staticmethod
    def healthy(node):
        try:
            with httpx.Client(timeout=3,trust_env=False) as client:
                health=client.get(f"http://127.0.0.1:{node['port']}"+node.get("health_path","/health"))
                if not 200 <= health.status_code < 300: return False
                if node.get("kind","runner")=="api": return True
                version=client.get(f"http://127.0.0.1:{node['port']}/version")
                return version.status_code==200 and version.json().get("version")==node["tag"]
        except (httpx.HTTPError,ValueError): return False

    def remove(self, identifier):
        self.owned(identifier)
        self.engine("rm", "-f", identifier)

    def ensure(self):
        if not self.operation.acquire(blocking=False):
            return
        try:
            if self.state.get("build",{}).get("status") in {"Building","Deploying"}:
                self.state["build"].update(status="Failed",message="Controller stopped during build/deployment. Inspect the pool and retry with a new tag.")
            # Reconcile a prior interrupted rollout before admitting new operations.
            for rollout in self.state["refreshes"]:
                if rollout["Status"] in {"Pending", "InProgress"}:
                    rollout.update(Status="Failed", StatusReason="Management stopped during rollout; inspect the serving pool and retry")
            alive = []
            for node in self.state["instances"]:
                try:
                    if self.owned(node["id"])["State"]["Running"]:
                        node["healthy"] = self.healthy(node)
                        alive.append(node)
                except RuntimeError:
                    pass
            self.state["instances"] = alive
            known = {node["id"] for node in alive}
            ids = self.engine("ps", "-aq", "--no-trunc", "--filter", f"label=cloudrunner.project={self.pool}",
                              "--filter", "label=cloudrunner.role=worker")
            for identifier in ids.splitlines():
                if identifier not in known:
                    self.remove(identifier)
            if not alive:
                tag = self.state["current_tag"] or os.getenv("LOCAL_INITIAL_RELEASE", "v1")
                image = self.image(tag)
                node = self.spawn(tag, image)
                self.state.update(instances=[node], desired=1, current_tag=tag, current_image=image)
                self.state["known_good"][tag] = image
                self.event(f"Started {node['name']} with release {tag}")
            self.save()
        finally:
            self.operation.release()

    def deploy(self, tag, rollback=False):
        if not self.operation.acquire(blocking=False):
            raise ValueError("A deployment or scaling operation is already active")
        try:
            image = self.image(tag)
            if rollback and self.state["known_good"].get(tag) != image:
                raise ValueError("Rollback requires a previously healthy image; use Deploy for new releases")
            rollout = {"InstanceRefreshId": uuid.uuid4().hex, "Status": "Pending",
                       "PercentageComplete": 0, "StatusReason": "Waiting for healthy replacement runners",
                       "StartTime": now(), "tag": tag, "image": image}
            with self.lock:
                self.state["refreshes"].insert(0, rollout)
                self.state["refreshes"] = self.state["refreshes"][:50]
                self.save()
            threading.Thread(target=self._rollout, args=(rollout, tag, image, rollback), daemon=True).start()
            return {"refresh_id": rollout["InstanceRefreshId"], "tag": tag, "status": "Pending"}
        except Exception:
            self.operation.release()
            raise

    def _rollout(self, rollout, tag, image, rollback=False, release_operation=True):
        previous = copy.deepcopy(self.state["instances"])
        replacements = []
        try:
            rollout["Status"] = "InProgress"
            self.save()
            # Keep the entire previous pool for automatic rollback until all replacements pass health.
            for index in range(max(1,len(previous))):
                replacements.append(self.spawn(tag, image))
                rollout["PercentageComplete"] = round(70 * (index + 1) / max(1,len(previous)))
                self.save()
            with self.lock:
                self.state["instances"] = replacements
                self.save()  # Gateway begins routing only to the ready replacement pool.
            time.sleep(17)  # Drain requests already assigned to the old pool (hard runner deadline is 7s).
            if not all(self.healthy(node) for node in replacements):
                raise RuntimeError("Replacement readiness failed after routing; restoring previous pool")
            with self.lock:
                self.state.update(current_tag=tag, current_image=image)
                self.state["known_good"][tag] = image
                self.high=self.low=0
                self.last_scale=time.monotonic()
                self.state["desired"]=len(replacements)
                rollout.update(Status="Successful", PercentageComplete=100, StatusReason="Every replacement runner passed readiness and the old pool drained")
                self.save()
            self.event(f"{'Rolled back' if rollback else 'Deployed'} release {tag}")
            for node in previous:
                try:
                    self.remove(node["id"])
                except Exception as exc:
                    self.event(f"Retired runner cleanup failed: {exc}", "Failed")
        except Exception as exc:
            with self.lock:
                self.state["instances"] = previous
                rollout.update(Status="Failed", StatusReason=str(exc))
                self.save()
            # Allow any requests already sent to a replacement to finish before cleanup.
            if replacements:
                time.sleep(17)
            for node in replacements:
                try:
                    self.remove(node["id"])
                except Exception:
                    pass
            self.event(f"Deployment {tag} failed; restored previous serving pool: {exc}", "Failed")
        finally:
            if release_operation: self.operation.release()

    def monitor(self):
        nodes = copy.deepcopy(self.state["instances"])
        if not nodes:
            self.ensure()
            return
        raw = self.engine("ps", "--no-trunc", "--filter", f"label=cloudrunner.project={self.pool}", "--format", "{{json .}}")
        listed=[json.loads(line) for line in raw.splitlines()]
        ids=[item["ID"] for item in listed]
        owners={}
        for item in listed:
            labels=item.get("Labels","")
            if isinstance(labels,str): labels=dict(part.split("=",1) for part in labels.split(",") if "=" in part)
            owners[item["ID"]]=labels.get("cloudrunner.instance")
        if not ids:
            raise RuntimeError("No project containers are running")
        samples = self.engine("stats", "--no-stream", "--no-trunc", "--format", "{{json .}}")
        # Jobs are sibling containers, so API-container CPU alone would miss the actual work.
        observed = [json.loads(line) for line in samples.splitlines()]
        observed = [sample for sample in observed if sample.get("ID") in ids and sample.get("CPUPerc", "").endswith("%")]
        if not observed:
            raise RuntimeError("Docker returned no CPU observations; metrics were not fabricated")
        cpu = sum(float(sample["CPUPerc"].rstrip("%")) for sample in observed)
        stamp = now()
        measured={sample["ID"]:float(sample["CPUPerc"].rstrip("%")) for sample in observed}
        for node in nodes:
            node["healthy"] = self.healthy(node)
            if node["id"] in measured:
                work=measured[node["id"]]+sum(value for identifier,value in measured.items() if owners.get(identifier)==node["name"])
                node["cpu_percent"]=round(work/node.get("cpu_limit",1),1)
        with self.lock:
            health = {node["id"]: node["healthy"] for node in nodes}
            for node in self.state["instances"]:
                if node["id"] in health:
                    node["healthy"] = health[node["id"]]
                    sample=next(n for n in nodes if n["id"]==node["id"])
                    if "cpu_percent" in sample: node["cpu_percent"]=sample["cpu_percent"]
            self.state["cpu"].append({"Timestamp": stamp, "Average": cpu / max(.25, sum(n.get("cpu_limit",1) for n in nodes))})
            self.state["counts"].append({"Timestamp": stamp, "Average": len(nodes)})
            self.state["cpu"] = self.state["cpu"][-720:]
            self.state["counts"] = self.state["counts"][-720:]
            self.state["monitor_error"] = None
            self.save()
        # Docker CPU% is 100% per host CPU core, averaged per runner's 1-core job budget.
        average = cpu / sum(n.get("cpu_limit",1) for n in nodes)
        self.high = self.high + 1 if average >= self.target else 0
        self.low = self.low + 1 if average < 25 else 0
        target = len(nodes)
        if self.high >= 3 and target < self.maximum:
            target += 1
        elif self.low >= 12 and target > self.minimum:
            target -= 1
        if target != len(nodes) and time.monotonic() - self.last_scale >= (self.scale_out_cooldown if target > len(nodes) else self.scale_in_cooldown) and self.operation.acquire(blocking=False):
            try:
                self.state["scale_reason"] = (f"CPU {average:.1f}% of configured quota; " +
                    (f"3 samples at or above {self.target}%" if target>len(nodes) else "12 samples below 25%"))
                self._scale(target)
                self.last_scale = time.monotonic()
                self.high = self.low = 0
            finally:
                self.operation.release()

    def _scale(self, target):
        nodes = copy.deepcopy(self.state["instances"])
        if target > len(nodes):
            node = self.spawn(self.state["current_tag"], self.state["current_image"])
            with self.lock:
                self.state["instances"].append(node)
                self.state["desired"] = target
                self.save()
            self.event(f"CPU scale-out: started {node['name']}; {target} replicas. {self.state.get('scale_reason','')}")
        else:
            retired = nodes[-1]
            with self.lock:
                self.state["instances"] = nodes[:-1]
                self.state["desired"] = target
                self.save()
            time.sleep(17)
            self.remove(retired["id"])
            self.event(f"CPU scale-in: removed {retired['name']}; {target} replicas. {self.state.get('scale_reason','')}")

    def status(self):
        with self.lock:
            state = copy.deepcopy(self.state)
        # Stable API schema shared with the original UI; values are observed local Docker state.
        try:
            traffic=json.loads(self.path.with_name("traffic.json").read_text())
        except (OSError,ValueError): traffic={}
        return {
            "traffic":traffic,
            "scaling_policy":{"minimum":self.minimum,"maximum":self.maximum,"target_cpu":self.target,
                "high_samples":3,"low_samples":12,"scale_out_cooldown":self.scale_out_cooldown,
                "scale_in_cooldown":self.scale_in_cooldown},
            "mode": "local", "endpoint": os.getenv("RUNNER_URL", "http://127.0.0.1:8000"),
            "current_tag": state["current_tag"], "current_image": state["current_image"],
            "group": {"AutoScalingGroupName": f"{self.pool} · Docker pool", "MinSize": self.minimum,
                      "MaxSize": self.maximum, "DesiredCapacity": state["desired"],
                      "Instances": [{"InstanceId": n["name"], "HealthStatus": "Healthy" if n["healthy"] else "Unhealthy",
                                     "LifecycleState": "InService" if n["healthy"] else "Unhealthy",
                                     "AvailabilityZone": "localhost", "CPUUtilization":n.get("cpu_percent"),"CPUQuota":n.get("cpu_limit",1), "LaunchTemplate": {"Version": n["tag"]}}
                                    for n in state["instances"]]},
            "refreshes": state["refreshes"], "monitor_error": state["monitor_error"],
            "application":{**self.spec(),"build":state.get("build"),"version":state["current_tag"]},
            "instance_releases": [{"instance_id": n["name"], "tag": n["tag"], "image": n["image"]} for n in state["instances"]],
        }

    def stop(self):
        with self.lock:
            self.state["instances"] = []
            self.state["desired"] = 0
            self.save()
        for role in ("worker", "job"):
            ids = self.engine("ps", "-aq", "--no-trunc", "--filter", f"label=cloudrunner.project={self.pool}",
                              "--filter", f"label=cloudrunner.role={role}")
            for identifier in ids.splitlines():
                self.remove(identifier)
        self.event("Stopped this project's local runner and job containers")
