from dataclasses import replace
from fractions import Fraction
import json

import numpy as np
import pytest

from host.functions import compile_samples, compile_range, shape_key, values
from host.interpolation import interpolate_codes, compile_interval
from host.model import Profile, Capabilities, compile_profile, offline_capabilities, fingerprint
from host.resource_usage import assess_resources
from host.sequences import compile_sequence, pack_step


def gaussian(bits=14, **kwargs):
    p=Profile(mode='function',dac_bits=bits,initial=5685 if bits==14 else 355,
              safe=5685 if bits==14 else 355,amplitude=0,timing_mode='gate',
              rise_ms=.07,hold_ms=0,fall_ms=.07,playback='linear',
              rise_function='I + (A-I)*(exp(-5*(1-t/T)**2)-exp(-5))/(1-exp(-5))',
              fall_function='I + (A-I)*(exp(-5*(t/T)**2)-exp(-5))/(1-exp(-5))')
    return replace(p,**kwargs)


def expand_linear(data, initial):
    result=[];start=initial
    for target,count in data.runs:
        result.extend(interpolate_codes(start,target,count,np.arange(count)))
        start=target
    return np.asarray(result,dtype=np.int64)


def test_reproduce_original_rle_counts_without_raising_8192_ceiling():
    for bits, expected in ((10,712),(14,11372)):
        p=gaussian(bits,playback='hold');key=shape_key(p)
        assert sum(len(compile_range(key,28000,offset,14000).runs) for offset in (0,14000))==expected
    with pytest.raises(ValueError,match='8192'):
        compile_samples(gaussian(playback='hold'))


@pytest.mark.parametrize('per_stage,max_error',[(64,2),(128,1)])
def test_reproduce_uniform_node_numeric_evidence(per_stage,max_error):
    p=gaussian();key=shape_key(p)
    one=np.rint(np.linspace(0,14000,per_stage+1)).astype(np.int64)
    nodes=np.r_[one,14000+one[1:]]
    codes=values(key,nodes,28000)[1].astype(np.int64)
    actual=np.concatenate([interpolate_codes(int(s),int(e),int(b-a),np.arange(b-a))
        for a,b,s,e in zip(nodes[:-1],nodes[1:],codes[:-1],codes[1:])])
    reference=values(key,np.arange(28000),28000)[1].astype(np.int64)
    assert len(actual)==28000
    assert int(np.max(np.abs(actual-reference)))==max_error


def test_adaptive_gaussian_every_sample_and_no_rle(monkeypatch):
    def no_rle(*args,**kwargs):raise AssertionError('interpolation must never compile raw RLE')
    monkeypatch.setattr('host.functions.compile_range',no_rle)
    p=gaussian();c=compile_samples(p,max_runs=2048)
    assert c.interpolation and len(c.runs)<=256 and c.error_limit==1
    assert c.total==c.checked_samples==28000 and c.max_error==1
    assert sum(n for _,n in c.runs[:c.rise_runs])==14000
    assert c.runs[c.rise_runs-1][0]==0 and c.runs[-1][0]==5685
    actual=expand_linear(c,p.initial)
    reference=values(shape_key(p),np.arange(28000),28000)[1].astype(np.int64)
    assert int(np.max(np.abs(actual-reference)))==1
    assert min(n for _,n in c.runs)>=32


@pytest.mark.parametrize('start,target,count',[
    (0,16383,32),(16383,0,32),(355,355,33),(0,1,32),(1,0,32),
    (0,16383,0xffffffff),(16383,0,0xffffffff),(16383,0,33)])
def test_integer_reference_width_sign_ties_and_endpoints(start,target,count):
    indices=np.unique(np.r_[0,1,count//2,count-1,count])
    actual=interpolate_codes(start,target,count,indices)
    for k,code in zip(indices,actual):
        exact=Fraction(start)+Fraction((target-start)*int(k),count)+Fraction(1,2)
        assert code==exact.numerator//exact.denominator
        assert 0<=code<=16383
    assert actual[0]==start and actual[-1]==target


@pytest.mark.parametrize('start,target',[(10,16380),(16380,10),(512,512)])
def test_minimum_segments_contiguous_no_missing_or_duplicate_clock(start,target):
    p=Profile(mode='ramp',playback='linear',dac_bits=14,initial=start,safe=start,
              amplitude=target,rise_ms=32/200000,hold_ms=0,fall_ms=32/200000,
              interpolation_error=1)
    c=compile_samples(p)
    assert c.runs==((target,32),(start,32)) and c.total==64
    actual=expand_linear(c,start)
    assert len(actual)==64 and actual[32]==target and actual[0]==start
    reference=values(shape_key(p),np.arange(64),64)[1].astype(np.int64)
    # The existing float sampler can land just below a mathematical .5 tie;
    # retain it as the original reference, while integer playback is exact.
    assert int(np.max(np.abs(actual-reference)))<=1


def test_short_segment_precision_and_capacity_refusals():
    with pytest.raises(ValueError,match='最少 32'):
        compile_samples(gaussian(rise_ms=31/200000))
    p=Profile(mode='function',function_layout='single',playback='linear',initial=0,
              function='where(t < T/2, 0, 1023)',function_ms=64/200000)
    with pytest.raises(ValueError,match='不会放宽精度'):
        compile_samples(p)
    with pytest.raises(ValueError,match='容量'):
        compile_samples(gaussian(),max_runs=128)
    report=assess_resources(gaussian(),offline_capabilities(dac_bits=14,max_runs=128))
    assert not report.fits and report.error_kind=='capacity' and report.required_runs is None


def test_every_sample_checked_across_chunks(monkeypatch):
    import host.interpolation as interpolation
    monkeypatch.setattr(interpolation,'CHUNK',17)
    p=Profile(mode='function',function_layout='single',playback='linear',initial=512,
              function='where(t == 73/200000000, 516, 512)',function_ms=128/200000)
    with pytest.raises(ValueError,match='精度'):
        compile_interval(shape_key(p),128,0,128,512,512,error_limit=1)


def test_profiles_default_compatible_and_settings_roundtrip():
    old=Profile(mode='ramp')
    assert 'playback' not in old.to_dict() and 'interpolation_error' not in old.to_dict()
    assert fingerprint(Profile.from_dict(old.to_dict()))==fingerprint(old)
    p=gaussian(interpolation_error=2)
    q=Profile.from_dict(json.loads(json.dumps(p.to_dict())))
    assert q.playback=='linear' and q.interpolation_error==2 and fingerprint(p)==fingerprint(q)
    with pytest.raises(ValueError,match='不支持分段线性插值'):
        compile_profile(p,Capabilities(dac_bits=14))
    compile_profile(p,offline_capabilities(dac_bits=14))
    r=assess_resources(p,offline_capabilities(dac_bits=14,max_runs=2048))
    assert r.fits and r.interpolation and r.checked_samples==28000 and r.max_error<=2
    assert '插值段' in r.summary() and '完整检查 28,000 点' in r.summary()


def sequence(waves,events=True,gap=0):
    cursor=0;steps=[]
    for i,w in enumerate(waves):
        steps.append(dict(waveform=w.to_dict(),kind='edge',input=i%2,edge='rising',repeat=1,
                          start_ms=cursor,delay_ms=0))
        cursor+=w.function_ms+gap
    return Profile(mode='sequence',name='插值序列',initial=waves[0].initial,safe=waves[0].safe,
                   dac_bits=waves[0].dac_bits,sequence_mode='events' if events else 'timeline',sequence_steps=steps)


def constant(playback):
    return Profile(mode='function',function_layout='single',playback=playback,initial=512,
                   function='I',function_ms=64/200000)


def test_event_mixed_modes_and_reuse_use_actual_pool_capacity():
    p=sequence([constant('hold'),constant('linear'),constant('linear')])
    c=compile_sequence(p,max_runs=2)
    assert c.interpolation and len(c.runs)==2
    assert [s[-1] for s in c.steps]==[0,1,1]
    assert [s[4] for s in c.steps]==[0,1,1]
    assert all(len(pack_step(s))==24 for s in c.steps)
    with pytest.raises(ValueError,match='容量'):compile_sequence(p,max_runs=1)
    with pytest.raises(ValueError,match='不支持分段线性插值'):
        compile_profile(p,Capabilities(sequence_steps=64))
    report=assess_resources(p,offline_capabilities(sequence_steps=64,max_runs=2))
    assert report.fits and report.required_runs==2 and report.required_steps==3


def test_linear_timeline_waits_repeats_and_mixed_rejection():
    w=constant('linear');p=sequence([w,w],events=False,gap=32/200000)
    p.sequence_steps[1]['repeat']=3
    c=compile_sequence(p)
    assert c.interpolation and c.total==64+32+3*64 and len(c.runs)==5
    assert all(n>=32 for _,n in c.runs)
    assert list(expand_linear(c,512))==[512]*c.total
    with pytest.raises(ValueError,match='不能混合'):
        compile_sequence(sequence([w,constant('hold')],events=False))
    with pytest.raises(ValueError,match='空隙仅 1 拍'):
        compile_sequence(sequence([w,w],events=False,gap=1/200000))


def test_gate_hold_not_stored_and_pool_boundary_preserved():
    w=gaussian();s=dict(waveform=w.to_dict(),kind='gate',input=1,edge='rising',repeat=1,delay_ms=0)
    p=Profile(mode='sequence',dac_bits=14,initial=w.initial,safe=w.safe,sequence_mode='events',sequence_steps=[s,s])
    c=compile_sequence(p);single=compile_samples(w)
    assert c.runs==single.runs and c.steps[0][4]==c.steps[1][4]
    assert c.steps[0][6]==single.rise_runs and c.steps[0][8]==14000
    assert sum(n for _,n in c.runs)==28000
