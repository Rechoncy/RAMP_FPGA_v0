import shutil
import struct
import subprocess
from pathlib import Path
import numpy as np
import pytest
from host.board import Board, generate_xdc, ports_for_counts, validate
from host.functions import compile_samples
from host.model import Profile, Capabilities, compile_profile
from host.protocol import Device, ARM, ABORT, BEGIN_RUNS, RUN
from host.simulator import Simulator

ROOT=Path(__file__).resolve().parents[1]


@pytest.mark.parametrize('bits',[10,12,14])
def test_width_quantization_roundtrip_and_device_bounds(bits):
    maximum=(1<<bits)-1; mid=1<<(bits-1)
    p=Profile(mode='function',function_layout='single',dac_bits=bits,initial=mid,safe=maximum,
              function=f'where(t<T/2,0,{maximum}-.5)',function_ms=.00002)
    samples=compile_samples(p)
    assert samples.runs==((0,2),(maximum,2))
    assert samples.max_error==.5
    assert Profile.from_dict(p.to_dict()).dac_bits==bits
    sim=Simulator(8,8,bits); d=Device(sim)
    assert d.caps.dac_bits==bits and d.status()['code']==mid
    d.upload(p); assert d.status()['code']==mid
    d.command(ARM); sim.external_trigger(); sim.started-=1
    assert d.status()['state']==1 and d.status()['code']==mid
    d.command(ABORT); assert d.status()['code']==maximum
    p.function=str(maximum+1)
    with pytest.raises(ValueError,match='原码'): compile_samples(p)
    p.function='I'; p.dac_bits=12 if bits==10 else 10
    with pytest.raises(ValueError,match='方案为'): d.upload(p)
    # Reject overflow before truncating into the parameterized memory width.
    with pytest.raises(RuntimeError):
        d.command(BEGIN_RUNS,struct.pack('<HHHHIBBBBB',maximum+1,mid,1,1,3,0,0,0,0,1))
    d.command(BEGIN_RUNS,struct.pack('<HHHHIBBBBB',mid,maximum,1,1,3,0,0,0,0,1))
    with pytest.raises(RuntimeError): d.command(RUN,struct.pack('<HHI',0,maximum+1,1))


@pytest.mark.parametrize('bits',[12,14])
def test_wide_ramp_cache_and_xdc_mapping(bits):
    mid=1<<(bits-1); maximum=(1<<bits)-1
    p=Profile(mode='ramp',dac_bits=bits,initial=mid,amplitude=maximum,
              rise_ms=.00001,hold_ms=.000005,fall_ms=.00001)
    result=compile_samples(p)
    assert list(np.repeat([v for v,n in result.runs],[n for v,n in result.runs]))==[
        mid,(mid+maximum+1)//2,maximum,maximum,(mid+maximum+1)//2]
    p.dac_bits=10
    with pytest.raises(ValueError): compile_samples(p)  # cache cannot reuse wide result
    b=Board(dac_bits=bits,dac_count=8,trigger_count=8,dac_delay_min=-1,dac_delay_max=2)
    assert b.pins[f'dac_data[{bits}]']=='U18'  # DAC 2 D0, independent of stride
    assert f'dac_data[10]' not in b.pins
    xdc=generate_xdc(b)
    assert 'set_property IOSTANDARD LVCMOS33 [get_ports {dac_data[10]}]' in xdc
    assert f'dac_data[{bits*8-1}]' in xdc
    assert len(ports_for_counts(8,8,bits))==5+8+8*(bits+1)
    assert any(f'dac_data[{bits-1}]' in message for message in validate(b,True))
    assert Board.from_dict(b.to_dict()).pins==b.pins
    with pytest.raises(ValueError,match='旧分段'):
        compile_profile(Profile(dac_bits=bits),Capabilities(dac_bits=bits))


@pytest.mark.parametrize('bits',[10,12,14])
def test_rtl_full_width_output_status_and_trigger(bits):
    if not shutil.which('iverilog'): pytest.skip('Icarus not installed')
    output=ROOT/'build'/f'width_{bits}.vvp'
    subprocess.run(['iverilog','-g2012','-s','tb_expanded',f'-Ptb_expanded.BITS={bits}',
        '-Ptb_expanded.DACS=8','-Ptb_expanded.TRIGS=8','-o',str(output),
        'rtl/common/controller.v','rtl/common/linear_engine.v','rtl/common/scan_engine.v','rtl/common/program_sequencer.v','rtl/common/gate_engine.v','rtl/common/sequence_engine.v','rtl/common/sample_engine.v',
        'tests/rtl/tb_expanded.v'],cwd=ROOT,check=True,capture_output=True,text=True)
    r=subprocess.run(['vvp',str(output)],cwd=ROOT,check=True,capture_output=True,text=True,timeout=20)
    assert 'PASS expanded' in r.stdout
