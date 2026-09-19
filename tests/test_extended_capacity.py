import pytest
from host.functions import compile_samples,compiler_capacity
from host.model import Profile,Capabilities
from host.resource_usage import assess_resources


def test_large_explicit_device_capacity_does_not_hit_old_rle_compiler_ceiling():
    p=Profile(mode='function',function_layout='single',function='where(cos(pi*t*200000000)>0,0,1023)',function_ms=.05)
    with pytest.raises(ValueError,match='8192'):compile_samples(p)
    samples=compile_samples(p,max_runs=32768)
    assert len(samples.runs)==10000 and samples.total==10000
    assert all(n==1 for _,n in samples.runs)


def test_old_hardware_still_counts_exact_requirements_before_capacity_rejection():
    p=Profile(mode='ramp',initial=100,safe=100,amplitude=200,rise_ms=.002,hold_ms=.002,fall_ms=.002)
    assert len(compile_samples(p,max_runs=2).runs)>2
    assert not assess_resources(p,Capabilities(max_runs=2)).fits


@pytest.mark.parametrize('value',[0,-1,True,65536,1.1,'32768'])
def test_protocol_capacity_bounds(value):
    with pytest.raises(ValueError):compiler_capacity(value)
