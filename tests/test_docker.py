"""Opt-in integration suite. Never substitutes host execution for unavailable Docker."""
import importlib.util
import os
from pathlib import Path
import pytest
pytestmark=pytest.mark.skipif(os.getenv('RUN_DOCKER_TESTS')!='1',reason='Opt-in: requires Docker and built sandbox image')
@pytest.fixture
def runner():
    pytest.importorskip('docker')
    path=Path(__file__).parents[1]/'runner-api/main.py'
    spec=importlib.util.spec_from_file_location('docker_runner',path)
    m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m

def test_stdout_and_stdin(runner):
    r=runner.execute(runner.Program(code='print(input())',stdin='hello'))
    assert r['stdout']=='hello\n';assert r['exit_code']==0;assert not r['timed_out']
def test_timeout(runner):
    r=runner.execute(runner.Program(code='while True: pass'))
    assert r['timed_out']
def test_readonly_and_nonroot(runner):
    r=runner.execute(runner.Program(code='import os\nprint(os.getuid())\nopen("/blocked", "w")'))
    assert r['stdout'].strip()=='65534';assert r['exit_code']!=0
def test_network_blocked(runner):
    r=runner.execute(runner.Program(code='import socket\nsocket.create_connection(("1.1.1.1",80),timeout=1)'))
    assert r['exit_code']!=0
def test_output_bounded(runner):
    r=runner.execute(runner.Program(code='while True: print("x"*1000)'))
    assert len(r['stdout'].encode())<=65536

def test_containers_cleaned(runner):
    import docker
    runner.execute(runner.Program(code='print(1)'))
    c=docker.from_env();assert not c.containers.list(all=True,filters={'label':'cloudrunner.job=true'});c.close()
