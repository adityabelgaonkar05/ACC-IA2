import importlib.util
from pathlib import Path
import pytest
pytest.importorskip('httpx')
pytest.importorskip('fastapi')
from fastapi.testclient import TestClient

def module(name,path):
    spec=importlib.util.spec_from_file_location(name,Path(__file__).parents[1]/path)
    mod=importlib.util.module_from_spec(spec);spec.loader.exec_module(mod);return mod
@pytest.fixture
def management(monkeypatch):
    monkeypatch.setenv('CLOUDRUNNER_MODE','mock')
    m=module('management_test','management-api/main.py')
    return m,TestClient(m.app)

def test_mutation_needs_token(management):
    _,c=management
    assert c.post('/api/deploy',json={'tag':'a'*40}).status_code==403

def test_mock_never_deploys(management):
    _,c=management;t=c.get('/api/session').json()['token']
    assert c.post('/api/deploy',json={'tag':'a'*40},headers={'X-CloudRunner-Token':t}).status_code==409
    assert c.get('/api/metrics').json()=={'cpu':[],'instances':[]}
    assert c.get('/api/releases').json()==[]

def test_cross_origin_rejected(management):
    _,c=management
    assert c.get('/api/session',headers={'Origin':'https://evil.example'}).status_code==403

def test_dns_rebinding_host_rejected(management):
    _,c=management
    assert c.get('/api/session',headers={'Host':'evil.example'}).status_code==403

def test_release_validation(management):
    _,c=management;t=c.get('/api/session').json()['token']
    assert c.post('/api/deploy',json={'tag':'../invalid'},headers={'X-CloudRunner-Token':t}).status_code==422

def test_load_bounds(management):
    _,c=management;t=c.get('/api/session').json()['token']
    assert c.post('/api/load',json={'duration':9999,'concurrency':99},headers={'X-CloudRunner-Token':t}).status_code==422

def test_runner_auth_and_limits(monkeypatch):
    monkeypatch.setenv('RUNNER_API_KEY','test-secret')
    m=module('runner_test','runner-api/main.py');c=TestClient(m.app)
    assert c.get('/version').status_code==200
    assert c.post('/execute',json={'code':'print(1)'}).status_code==401
    assert c.post('/execute',json={'code':'x'*16385},headers={'X-API-Key':'test-secret'}).status_code==422
    assert c.post('/execute',json={'code':'x','language':'cpp'},headers={'X-API-Key':'test-secret'}).status_code==422


def test_request_size_bounded(management):
    _,c=management;t=c.get('/api/session').json()['token']
    assert c.post('/api/execute',content=b'x'*262145,headers={'X-CloudRunner-Token':t}).status_code==413


def test_runner_disposable_security_flags_and_cleanup(monkeypatch):
    import sys
    import json
    from unittest.mock import Mock
    sdk=Mock();client=sdk.from_env.return_value;container=client.containers.create.return_value
    container.wait.return_value={'StatusCode':0}
    container.logs.return_value=json.dumps({'stdout':'ok\n','stderr':'','exit_code':0,'timed_out':False}).encode()
    monkeypatch.setitem(sys.modules,'docker',sdk)
    m=module('runner_flags','runner-api/main.py')
    r=m.execute(m.Program(code='print("ok")'))
    assert r['stdout']=='ok\n'
    flags=client.containers.create.call_args.kwargs
    assert flags['user']=='65534:65534'
    assert flags['network_disabled'] and flags['read_only']
    assert flags['cap_drop']==['ALL']
    assert flags['security_opt']==['no-new-privileges:true']
    assert flags['pids_limit']==32 and flags['mem_limit']=='128m'
    assert flags['nano_cpus']==500_000_000
    assert 'volumes' not in flags and 'environment' not in flags
    container.remove.assert_called_once_with(force=True)
    client.close.assert_called_once()


def test_runner_cleanup_on_timeout(monkeypatch):
    import sys
    from unittest.mock import Mock
    sdk=Mock();client=sdk.from_env.return_value;container=client.containers.create.return_value
    container.wait.side_effect=TimeoutError()
    container.logs.return_value=b''
    monkeypatch.setitem(sys.modules,'docker',sdk)
    m=module('runner_timeout','runner-api/main.py')
    r=m.execute(m.Program(code='while True: pass'))
    assert r['timed_out']
    container.kill.assert_called_once()
    container.remove.assert_called_once_with(force=True)


def test_runner_health_requires_engine_and_image(monkeypatch):
    import sys
    from unittest.mock import Mock
    sdk=Mock();monkeypatch.setitem(sys.modules,'docker',sdk)
    m=module('runner_readiness','runner-api/main.py');c=TestClient(m.app)
    assert c.get('/health').json()=={'status':'ok'}
    sdk.from_env.return_value.images.get.side_effect=RuntimeError('missing sandbox')
    assert c.get('/health').status_code==503


def test_runner_passes_payload_without_docker_attach(monkeypatch):
    import sys, json
    from unittest.mock import Mock
    sdk=Mock(); client=sdk.from_env.return_value; container=client.containers.create.return_value
    container.wait.return_value={'StatusCode':0}
    container.logs.return_value=b'{"stdout":"ok","stderr":"","exit_code":0,"timed_out":false}'
    monkeypatch.setitem(sys.modules,'docker',sdk)
    m=module('runner_payload','runner-api/main.py')
    program=m.Program(code='print(input()) # "quotes" 🚀',stdin='some input\n')
    m.execute(program)
    args,flags=client.containers.create.call_args
    assert args[1][:3]==['python','-I','/sandbox.py']
    assert json.loads(args[1][3])==program.model_dump()
    assert flags['use_config_proxy'] is False
    assert 'stdin_open' not in flags
    container.attach_socket.assert_not_called()
    container.remove.assert_called_once_with(force=True)


def test_runner_preserves_docker_error_message(monkeypatch):
    monkeypatch.setenv('RUNNER_API_KEY','test-secret')
    m=module('runner_docker_failure','runner-api/main.py')
    def fail(_): raise RuntimeError('Docker resource configuration rejected')
    monkeypatch.setattr(m,'execute',fail)
    c=TestClient(m.app)
    response=c.post('/execute',json={'code':'print(1)'},headers={'X-API-Key':'test-secret'})
    assert response.status_code==503
    assert 'Docker resource configuration rejected' in response.json()['detail']


def test_management_preserves_upstream_execution_error(management,monkeypatch):
    import httpx
    m,c=management
    async def reply(request):
        return httpx.Response(503,json={'detail':'Execution engine failed: example Docker error'})
    transport=httpx.MockTransport(reply)
    original=httpx.AsyncClient
    monkeypatch.setattr(m.httpx,'AsyncClient',lambda **kw: original(transport=transport,**kw))
    token=c.get('/api/session').json()['token']
    response=c.post('/api/execute',json={'code':'print(1)'},headers={'X-CloudRunner-Token':token})
    assert response.status_code==503
    assert response.json()['detail']=='Execution engine failed: example Docker error'
