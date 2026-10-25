import importlib.util
from pathlib import Path
import pytest

spec=importlib.util.spec_from_file_location('ramp_load',Path(__file__).parents[1]/'scripts/ramp_load.py')
ramp=importlib.util.module_from_spec(spec);spec.loader.exec_module(ramp)


def ready():
    return {'application':{'kind':'api','load_path':'/work','cpu_limit':.25},
            'group':{'Instances':[{'InstanceId':'one'}],'MaxSize':5},'verified':True}


def test_ramp_increases_every_twelve_seconds_and_stays_bounded():
    assert [ramp.rate_at(t,60) for t in (0,11,12,24,36,48,59,60)]==[1,1,2,3,4,6,6,6]
    assert ramp.rate_at(18,90)==2


def test_ready_check_accepts_healthy_single_replica_demo():
    ramp.check_ready(ready())


@pytest.mark.parametrize('field,value', [('kind','runner'),('cpu_limit',1),('load_path','/delete')])
def test_refuses_wrong_application_configuration(field,value):
    state=ready();state['application'][field]=value
    with pytest.raises(ValueError):ramp.check_ready(state)


def test_refuses_unhealthy_or_already_scaled_pool():
    state=ready();state['verified']=False
    with pytest.raises(ValueError):ramp.check_ready(state)
    state=ready();state['group']['Instances']*=2
    with pytest.raises(ValueError):ramp.check_ready(state)
