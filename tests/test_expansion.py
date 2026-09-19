import copy
import struct
import numpy as np
import pytest
from host.model import Profile,Capabilities,compile_profile
from host.functions import compile_samples
from host.board import Board,LEGACY,ports_for_counts,generate_xdc,validate
from host.protocol import Device,ARM,TRIGGER,ABORT,DISARM,BEGIN_RUNS
from host.simulator import Simulator


def test_initial_code_is_used_by_ramp_function_cache_and_download():
    p=Profile(mode='ramp',initial=200,safe=333,amplitude=400,rise_ms=.00002,hold_ms=0,fall_ms=.00002)
    r=compile_samples(p)
    assert list(np.repeat([c for c,n in r.runs],[n for c,n in r.runs]))==[200,250,300,350,400,350,300,250]
    p.initial=100
    assert compile_samples(p).runs[0][0]==100
    p.mode='function'; p.function_layout='single'; p.function='I+100*t/T'; p.function_ms=.00002
    assert [c for c,n in compile_samples(p).runs]==[100,125,150,175]
    sim=Simulator(); dev=Device(sim); dev.upload(p)
    assert dev.status()['code']==100
    dev.command(ARM); dev.command(ABORT); assert dev.status()['code']==333
    dev.command(DISARM); assert dev.status()['code']==100
    p.initial=-1
    with pytest.raises(ValueError,match='初始码'): compile_samples(p)


def test_independent_trigger_routing_polarity_and_channel_limits():
    sim=Simulator(8,4); d=Device(sim)
    assert (d.caps.dac_count,d.caps.trigger_count,d.caps.trigger_routing)==(8,4,True)
    p=Profile(mode='function',function_layout='single',initial=700,safe=222,function='I',channel=7,trigger_input=2,trigger_edge='falling')
    d.upload(p); d.command(ARM)
    sim.external_trigger(1,'falling'); assert d.status()['state']==1
    sim.external_trigger(2,'rising'); assert d.status()['state']==1
    sim.external_trigger(2,'falling'); sim.started-=1
    assert d.status()['state']==1 and d.status()['code']==700
    assert d.status()['trigger_input']==2 and d.status()['trigger_edge']=='falling'
    d.command(ABORT); assert d.status()['code']==222
    p.channel=8
    with pytest.raises(ValueError): d.upload(p)
    p.channel=7; p.trigger_input=4
    with pytest.raises(ValueError): d.upload(p)
    p.trigger='software'; d.upload(p)  # external selector is ignored in software mode
    d.command(ARM); d.command(TRIGGER)


def test_old_profile_trigger_mapping_and_old_firmware_fallback():
    p=Profile(mode='function',function_layout='single',initial=512,safe=512,channel=1)
    value=p.to_dict(); del value['trigger_input']; del value['trigger_edge']
    migrated=Profile.from_dict(value)
    assert migrated.trigger_input==1 and migrated.trigger_edge=='rising'
    old=Capabilities(dac_count=2,trigger_count=2,trigger_routing=False)
    compile_profile(migrated,old)
    migrated.trigger_input=0
    with pytest.raises(ValueError,match='旧固件'): compile_profile(migrated,old)


def test_board_migration_expansion_and_per_dac_timing():
    old=Board().to_dict(); old['schema']=1; old['pins']=dict(LEGACY)
    old['io_overrides']={'fm_value[0]':'LVCMOS18'}
    b=Board.from_dict(old)
    assert b.pins['trigger_in[0]']=='F13' and b.pins['dac_data[10]']=='U18'
    assert b.io_overrides['dac_data[10]']=='LVCMOS18'
    b.dac_count=8; b.trigger_count=4
    assert len(ports_for_counts(8,4))==97
    x=generate_xdc(b)
    assert 'dac_data[79]' in x and 'dac_clk_8' in x and 'trigger_in[3]' in x
    assert 'dipole_ramp' not in x and 'fm_value' not in x
    missing=validate(b,True)
    assert any('dac_data[79]' in e for e in missing)
    assert any('trigger_in[3]' in e for e in missing)
    b.dac_timing={'7':{'min':-1.2,'max':2.4,'falling':True}}
    x=generate_xdc(b)
    assert 'set_output_delay -clock [get_clocks dac_clk_8] -clock_fall -min -1.200000' in x
    assert any('DAC 1' in e for e in validate(b,True))
    assert not any('DAC 8' in e for e in validate(b,True))
    b.dac_count=9
    with pytest.raises(ValueError): generate_xdc(b)


def test_firmware_rejects_unavailable_trigger_and_bad_edge_before_commit():
    d=Device(Simulator(4,1))
    for channel,index,edge,flags in [(4,0,0,1),(0,1,0,1),(0,0,2,1),(0,0,0,2)]:
        with pytest.raises(RuntimeError):
            d.command(BEGIN_RUNS,struct.pack('<HHHHIBBBBB',123,456,1,1,42,1,channel,index,edge,flags))


@pytest.mark.parametrize('dacs,triggers',[(1,1),(4,8),(8,4)])
def test_rtl_expansion_limits(dacs,triggers):
    import shutil,subprocess
    from tests.test_rtl import ROOT
    if not shutil.which('iverilog'): pytest.skip('Icarus not installed')
    output=ROOT/'build'/f'expanded_{dacs}_{triggers}.vvp'
    subprocess.run(['iverilog','-g2012','-s','tb_expanded',f'-Ptb_expanded.DACS={dacs}',f'-Ptb_expanded.TRIGS={triggers}',
                    '-o',str(output),'rtl/common/controller.v','rtl/common/linear_engine.v','rtl/common/scan_engine.v','rtl/common/program_sequencer.v','rtl/common/gate_engine.v','rtl/common/sequence_engine.v','rtl/common/sample_engine.v','tests/rtl/tb_expanded.v'],cwd=ROOT,check=True,capture_output=True,text=True)
    result=subprocess.run(['vvp',str(output)],cwd=ROOT,check=True,capture_output=True,text=True,timeout=20)
    assert 'PASS expanded' in result.stdout


def test_rtl_trigger_synchronization_and_startup():
    from tests.test_rtl import run_tb
    run_tb('tb_trigger_sync',['rtl/common/trigger_sync.v'])
