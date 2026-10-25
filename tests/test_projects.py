import importlib.util
import json
from pathlib import Path
from unittest.mock import Mock

import httpx
import pytest
from fastapi.testclient import TestClient

from cloudrunner.projects import validate, snapshot
from cloudrunner.local import LocalManager


def specification(path):
    return dict(name='Example',path=str(path),command='sh run.sh',runtime='python',port=8080,
                health_path='/ready',load_path='/work',version='demo-v1',cpu_limit=.5,memory_mb=512)


def test_snapshot_excludes_secrets_and_symlinks(tmp_path):
    root=tmp_path/'source';root.mkdir()
    (root/'main.py').write_text('print(1)')
    (root/'.env').write_text('PRIVATE=secret')
    (root/'private.key').write_text('secret')
    (root/'.aws').mkdir();(root/'.aws'/'credentials').write_text('secret')
    (root/'linked').symlink_to(root/'main.py')
    spec=validate(specification(root));out=tmp_path/'snapshot'
    filename=snapshot(spec,out)
    assert (out/'main.py').exists()
    assert not (out/'.env').exists()
    assert not (out/'.aws').exists()
    assert not (out/'private.key').exists()
    assert not (out/'linked').exists()
    assert 'CMD ["sh", "run.sh"]' in (out/filename).read_text()
    assert 'USER 10001:10001' in (out/filename).read_text()


@pytest.mark.parametrize('path',['/','~'])
def test_refuses_broad_source_directories(path):
    with pytest.raises(ValueError):validate(specification(path))


@pytest.mark.parametrize('field,value',[('health_path','https://evil/'),('load_path','//evil/'),('load_path','http://evil/')])
def test_no_arbitrary_probe_targets(tmp_path,field,value):
    spec=specification(tmp_path);spec[field]=value
    with pytest.raises(ValueError):validate(spec)


def test_start_command_is_argument_vector_not_host_shell(tmp_path):
    spec=specification(tmp_path);spec['command']='python main.py --name "my API"'
    assert validate(spec)['argv']==['python','main.py','--name','my API']


def test_generic_container_enforces_quota_without_docker_socket(tmp_path):
    calls=[]
    def engine(*args,**kw):
        calls.append((args,kw))
        if args[0]=='run':return 'new-id'
        if args[0]=='inspect':return json.dumps([{'Config':{'Labels':{'cloudrunner.project':'cloudrunner'}},'NetworkSettings':{'Ports':{'8080/tcp':[{'HostPort':'30000'}]}}}])
        raise AssertionError(args)
    m=LocalManager(engine=engine,path=tmp_path/'state.json')
    spec=validate(specification(tmp_path));m.state['release_specs']={'sha256:new':spec}
    m.healthy=Mock(return_value=True)
    node=m.spawn('demo-v1','sha256:new')
    args,kw=calls[0]
    assert args[args.index('--cpus')+1]=='.5' or args[args.index('--cpus')+1]=='0.5'
    assert '--read-only' in args and '--cap-drop' in args
    assert '-v' not in args
    assert 'RUNNER_API_KEY' not in args
    assert args[-2:]==('sha256:new','run.sh')
    assert args[args.index('--entrypoint')+1]=='sh'
    assert node['health_path']=='/ready' and node['kind']=='api'


def test_generic_health_does_not_require_version_endpoint(monkeypatch):
    original=httpx.Client;seen=[]
    def reply(request):
        seen.append(request.url.path)
        return httpx.Response(204)
    monkeypatch.setattr('cloudrunner.local.httpx.Client',lambda **kwargs: original(transport=httpx.MockTransport(reply),**kwargs))
    assert LocalManager.healthy({'kind':'api','port':12345,'health_path':'/ready'})
    assert seen==['/ready']


def test_cpu_normalizes_to_configured_quota(tmp_path):
    def engine(*args,**kwargs):
        if args[0]=='ps':return json.dumps({'ID':'worker'})
        if args[0]=='stats':return json.dumps({'ID':'worker','CPUPerc':'40%'})
        raise AssertionError(args)
    m=LocalManager(engine=engine,path=tmp_path/'state.json')
    m.state['instances']=[{'id':'worker','name':'worker','cpu_limit':.5,'healthy':True}]
    m.healthy=Mock(return_value=True);m._scale=Mock()
    m.monitor();m.monitor();m.monitor()
    assert m.state['cpu'][-1]['Average']==80
    m._scale.assert_called_once_with(2)
    assert '3 samples' in m.state['scale_reason']


def test_failed_build_preserves_current_application(tmp_path):
    m=LocalManager(engine=Mock(side_effect=RuntimeError('Docker build failed')),path=tmp_path/'state.json')
    m.state['current_tag']='old';m.state['build']={'status':'Building'}
    m.operation.acquire()
    m._build_application(validate(specification(tmp_path)))
    assert m.state['build']['status']=='Failed'
    assert m.state['current_tag']=='old'
    assert not m.operation.locked()


def test_gateway_proxies_arbitrary_routes_and_records_traffic(tmp_path,monkeypatch):
    spec=importlib.util.spec_from_file_location('generic_gateway',Path(__file__).parents[1]/'management-api/gateway.py')
    gateway=importlib.util.module_from_spec(spec);spec.loader.exec_module(gateway)
    monkeypatch.setattr(gateway,'state_path',lambda:tmp_path/'state.json')
    monkeypatch.setattr(gateway,'read_state',lambda:{'instances':[
        {'name':'a','healthy':True,'port':12345,'tag':'api-v1'},
        {'name':'b','healthy':True,'port':12346,'tag':'api-v1'}]})
    seen=[]
    class Upstream:
        async def request(self,method,url,**kwargs):
            seen.append((method,url,kwargs))
            return httpx.Response(201,json={'replica':url})
    with TestClient(gateway.app) as client:
        gateway.app.state.client=Upstream()
        r=client.post('/api/echo?x=1',json={'hello':'world'})
        assert r.status_code==201
        assert client.get('/work').status_code==201
    assert seen[0][0]=='POST' and seen[0][1].endswith('/api/echo?x=1')
    assert ':12345/' in seen[0][1] and ':12346/' in seen[1][1]
    counts=json.loads((tmp_path/'traffic.json').read_text())
    assert counts['a']['requests']==counts['b']['requests']==1
    assert counts['a']['active']==0 and counts['a']['durations_ms']
