import copy
import json
import threading
from unittest.mock import Mock

import pytest
from cloudrunner.local import LocalManager, TAG


@pytest.fixture
def manager(tmp_path, monkeypatch):
    monkeypatch.setenv("RUNNER_API_KEY", "test-secret")
    m = LocalManager(engine=Mock(), path=tmp_path / "state.json")
    m.state.update(instances=[{"id":"old", "name":"old", "tag":"v1", "image":"sha256:old", "port":9001, "healthy":True}],
                   current_tag="v1", current_image="sha256:old")
    m.state["known_good"]={"v1":"sha256:old"}
    return m


@pytest.mark.parametrize("tag", ["latest", "../evil", "$(id)", "v1;rm", "UPPER", "a"*65])
def test_invalid_release_tag(tag):
    assert not TAG.fullmatch(tag)


def test_rollback_requires_known_good_digest(manager):
    manager.image = Mock(return_value="sha256:new")
    with pytest.raises(ValueError, match="previously healthy"):
        manager.deploy("v2", rollback=True)
    assert not manager.operation.locked()


def test_overlapping_operations_rejected(manager):
    manager.operation.acquire()
    with pytest.raises(ValueError, match="already active"):
        manager.deploy("v2")
    manager.operation.release()


def test_deploy_is_pending_before_worker_runs(manager, monkeypatch):
    manager.image = Mock(return_value="sha256:new")
    thread = Mock()
    monkeypatch.setattr(threading, "Thread", Mock(return_value=thread))
    result = manager.deploy("v2")
    assert result["status"] == "Pending"
    assert manager.state["current_tag"] == "v1"
    assert manager.operation.locked()
    thread.start.assert_called_once()
    manager.operation.release()


def test_failed_rollout_preserves_serving_pool(manager, monkeypatch):
    monkeypatch.setattr("cloudrunner.local.time.sleep", lambda _: None)
    manager.spawn = Mock(side_effect=RuntimeError("readiness failed"))
    original = copy.deepcopy(manager.state["instances"])
    rollout={"Status":"Pending"}
    manager.state["refreshes"]=[rollout]
    manager.operation.acquire()
    manager._rollout(rollout,"v2","sha256:new")
    assert manager.state["instances"] == original
    assert manager.state["current_tag"] == "v1"
    assert rollout["Status"] == "Failed"
    assert not manager.operation.locked()


def test_successful_rollout_switches_only_ready_nodes(manager, monkeypatch):
    monkeypatch.setattr("cloudrunner.local.time.sleep", lambda _: None)
    node={"id":"new","name":"new","tag":"v2","image":"sha256:new","port":9002,"healthy":True}
    manager.spawn=Mock(return_value=node)
    manager.healthy=Mock(return_value=True)
    manager.remove=Mock()
    rollout={"Status":"Pending"};manager.state["refreshes"]=[rollout]
    manager.operation.acquire()
    manager._rollout(rollout,"v2","sha256:new")
    assert manager.state["instances"] == [node]
    assert manager.state["current_tag"] == "v2"
    assert manager.state["known_good"]["v2"] == "sha256:new"
    assert rollout["Status"] == "Successful"
    manager.remove.assert_called_once_with("old")


def test_cpu_controller_counts_jobs_and_excludes_other_projects(manager):
    def engine(*args, **kwargs):
        if args[0]=="ps": return '\n'.join(json.dumps({"ID": x}) for x in ["worker", "job"])
        if args[0]=="stats": return '\n'.join(json.dumps({"ID":x,"CPUPerc":cpu}) for x,cpu in [("worker","2%"),("job","80%"),("unrelated","500%")])
        raise AssertionError(args)
    manager.engine=engine
    manager.healthy=Mock(return_value=True)
    manager._scale=Mock()
    manager.monitor();manager.monitor()
    manager._scale.assert_not_called()
    manager.monitor()
    manager._scale.assert_called_once_with(2)
    assert manager.state["cpu"][-1]["Average"] == 82


def test_missing_stats_never_fabricated(manager):
    manager.engine.side_effect=[json.dumps({"ID":"worker"}), ""]
    with pytest.raises(RuntimeError,match="not fabricated"):
        manager.monitor()
    assert manager.state["cpu"] == []


def test_cannot_remove_another_projects_container(manager):
    manager.engine.return_value=json.dumps([{"Config":{"Labels":{"cloudrunner.project":"other"}}}])
    with pytest.raises(ValueError,match="outside"):
        manager.remove("unrelated")
    assert manager.engine.call_count==1


def test_saved_state_is_atomic_and_contains_no_api_key(manager):
    manager.save()
    contents=manager.path.read_text()
    assert json.loads(contents)["current_tag"]=="v1"
    assert "test-secret" not in contents
    assert not manager.path.with_suffix(".tmp").exists()


@pytest.mark.parametrize('count', range(1, 6))
def test_scale_out_advances_one_replica_but_never_exceeds_five(manager, count):
    nodes=[{'id':f'w{i}','name':f'w{i}','cpu_limit':.25,'healthy':True} for i in range(count)]
    manager.state['instances']=nodes
    def engine(*args,**kw):
        if args[0]=='ps': return '\n'.join(json.dumps({'ID':n['id']}) for n in nodes)
        if args[0]=='stats': return '\n'.join(json.dumps({'ID':n['id'],'CPUPerc':'24%'}) for n in nodes)
        raise AssertionError(args)
    manager.engine=engine;manager.healthy=Mock(return_value=True);manager._scale=Mock()
    for _ in range(3):manager.monitor()
    if count<5:manager._scale.assert_called_once_with(count+1)
    else:manager._scale.assert_not_called()
    assert manager.maximum==5 and manager.minimum==1


def test_fast_scale_out_keeps_conservative_scale_in_cooldown(manager,monkeypatch):
    nodes=[{'id':f'w{i}','name':f'w{i}','cpu_limit':.25,'healthy':True} for i in range(2)]
    manager.state['instances']=nodes
    def engine(*args,**kw):
        if args[0]=='ps':return '\n'.join(json.dumps({'ID':n['id']}) for n in nodes)
        if args[0]=='stats':return '\n'.join(json.dumps({'ID':n['id'],'CPUPerc':'0%'}) for n in nodes)
        raise AssertionError(args)
    manager.engine=engine;manager.healthy=Mock(return_value=True);manager._scale=Mock()
    manager.last_scale=100
    monkeypatch.setattr('cloudrunner.local.time.monotonic',lambda:110)
    for _ in range(12):manager.monitor()
    manager._scale.assert_not_called()
    monkeypatch.setattr('cloudrunner.local.time.monotonic',lambda:146)
    manager.monitor()
    manager._scale.assert_called_once_with(1)
