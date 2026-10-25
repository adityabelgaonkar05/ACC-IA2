"""Requires Docker and both project-labeled v1/v2 images. Runs in CI, opt-in locally."""
import importlib.util
import os
from pathlib import Path
import time
import pytest
import httpx
from cloudrunner.local import LocalManager

pytestmark=pytest.mark.skipif(os.getenv("RUN_LOCAL_TESTS")!="1", reason="Opt-in: real Docker and built v1/v2 images required")


def test_local_pool_gateway_deploy_and_rollback(tmp_path, monkeypatch):
    monkeypatch.setenv("RUNNER_API_KEY","integration-only-secret")
    monkeypatch.setenv("LOCAL_INITIAL_RELEASE","v1")
    m=LocalManager(path=tmp_path/"state.json")
    m.key="integration-only-secret"
    try:
        m.ensure()
        assert len(m.state["instances"])==1
        spec=importlib.util.spec_from_file_location("gateway_test",Path(__file__).parents[1]/"management-api/gateway.py")
        gateway=importlib.util.module_from_spec(spec);spec.loader.exec_module(gateway)
        monkeypatch.setattr(gateway,"read_state",lambda: __import__('json').loads(m.path.read_text()))
        from fastapi.testclient import TestClient
        with TestClient(gateway.app) as client:
            assert client.get("/health").status_code==200
            assert client.get("/version").json()["version"]=="v1"
            result=client.post("/execute",json={"code":"print(input())","stdin":"hello-local"},headers={"X-API-Key":m.key})
            assert result.status_code==200
            assert result.json()["stdout"]=="hello-local\n"
            for tag,rollback in [("v2",False),("v1",True)]:
                refresh=m.deploy(tag,rollback)
                deadline=time.monotonic()+90
                while time.monotonic()<deadline:
                    record=m.state["refreshes"][0]
                    # Reachability is checked while the replacement pool starts and drains.
                    assert client.get("/health").status_code==200
                    if record["Status"] in {"Successful","Failed"}:break
                    time.sleep(.5)
                assert record["Status"]=="Successful",record
                assert client.get("/version").json()["version"]==tag
    finally:
        # No unrelated Docker resources or global pruning.
        m.stop()


def test_generic_api_build_proxy_and_replica_scaling(tmp_path,monkeypatch):
    from cloudrunner.projects import validate
    monkeypatch.setenv('RUNNER_API_KEY','test-only')
    m=LocalManager(path=tmp_path/'state.json')
    source=tmp_path/'api-source'
    source.mkdir()
    (source/'main.py').write_text(
        'import os\nfrom fastapi import FastAPI\napp = FastAPI()\n'
        '@app.get("/health")\ndef health(): return {"status": "ok"}\n'
        '@app.get("/work")\ndef work(): return {"replica": os.getenv("INSTANCE_ID")}\n'
    )
    (source/'requirements.txt').write_text('fastapi==0.124.0\nuvicorn==0.38.0\n')
    (source/'run.sh').write_text('exec python -m uvicorn main:app --host 0.0.0.0 --port 8080\n')
    spec=validate({'name':'Generic integration API','path':str(source),
        'command':'sh run.sh','runtime':'python','port':8080,'health_path':'/health','load_path':'/work',
        'version':'generic-ci-v1','cpu_limit':.5,'memory_mb':256})
    try:
        m.state['build']={'status':'Building'}
        m.operation.acquire()
        m._build_application(spec)
        assert m.state['build']['status']=='Successful',m.state['build']
        node=m.state['instances'][0]
        assert node['kind']=='api'
        data=m.owned(node['id'])
        assert not any(mount['Destination']=='/var/run/docker.sock' for mount in data.get('Mounts',[]))
        assert data['HostConfig']['NanoCpus']==500_000_000
        m._scale(2)
        assert len(m.state['instances'])==2
        s=importlib.util.spec_from_file_location('generic_integration_gateway',Path(__file__).parents[1]/'management-api/gateway.py')
        g=importlib.util.module_from_spec(s);s.loader.exec_module(g)
        monkeypatch.setattr(g,'read_state',lambda:__import__('json').loads(m.path.read_text()))
        monkeypatch.setattr(g,'state_path',lambda:m.path)
        from fastapi.testclient import TestClient
        with TestClient(g.app) as client:
            results=[client.get('/work?seconds=0.05').json() for _ in range(4)]
            assert len({r['replica'] for r in results})==2
            assert client.get('/__cloudrunner/health').status_code==200
        m._scale(1)
        assert len(m.state['instances'])==1
    finally:
        m.stop()
