import numpy as np
import pytest
from host.model import Profile,Capabilities,compile_profile
from host.functions import compile_samples,values,shape_key,validate_shape
from host.protocol import Device,ARM,ABORT
from host.simulator import Simulator
from tests.test_rtl import run_tb


def shape(**kwargs):
    return Profile(mode='function',function_layout='single',initial=512,safe=512,**kwargs)


def expand(samples):
    return np.repeat([r[0] for r in samples.runs],[r[1] for r in samples.runs])


def test_raw_code_sampling_rounding_and_time_grid():
    p=shape(function='where(t<2/200000000,0.49,1022.5)',function_ms=.000025)
    result=compile_samples(p)
    assert result.total==5
    assert list(expand(result))==[0,0,1023,1023,1023]
    assert result.max_error==.5
    for value in (0,512,1023):
        assert compile_samples(shape(function=str(value))).runs==((value,2000),)


def test_ramp_one_amplitude_both_polarities():
    for target in (0,112,512,912,1023):
        p=Profile(mode='ramp',initial=512,safe=512,amplitude=target,rise_ms=.00002,hold_ms=.00001,fall_ms=.00002)
        expected=[512+(target-512)*x for x in (0,.25,.5,.75,1,1,1,.75,.5,.25)]
        assert list(expand(compile_samples(p)))==list(np.floor(np.array(expected)+.5))


@pytest.mark.parametrize('text',["__import__('os')",'t.__class__','[1]*1000','sin(t)[0]','2**1000','-1','1024','sqrt(-1)','1/(t-t)'])
def test_expression_rejects_code_execution_domain_and_out_of_range(text):
    with pytest.raises(ValueError): compile_samples(shape(function=text))


def test_lossless_capacity_rejection_and_chunk_boundary():
    p=shape(function='where(t<.00131072,512,100)',function_ms=2)
    r=compile_samples(p)
    assert r.runs==((512,262144),(100,137856))
    with pytest.raises(ValueError,match='8192'):
        compile_samples(shape(function='where((t*200000000)%2<.5,0,1023)',function_ms=.1))


def test_device_capability_channel_and_backward_compatibility():
    p=shape(channel=1)
    with pytest.raises(ValueError,match='新版固件'):
        compile_profile(p,Capabilities(max_runs=0))
    sim=Simulator(); device=Device(sim)
    device.upload(p)
    assert sim.active['samples'] and sim.active['divider']==1 and sim.active['channel']==1
    assert sum(n for _,n in sim.active['segments'])==2000
    device.command(ARM)
    with pytest.raises(RuntimeError): device.upload(p)
    device.command(ABORT)
    assert device.status()['code']==512 and device.status()['channel']==1


def test_rtl_sample_every_clock_and_full_capacity():
    run_tb('tb_samples',['rtl/common/sample_engine.v'])


def test_rtl_sample_protocol_and_dac_selection():
    run_tb('tb_sample_protocol',['rtl/common/controller.v','rtl/common/gate_engine.v','rtl/common/sequence_engine.v','rtl/common/sample_engine.v'])


def test_python_frames_through_uart_to_selected_dac():
    import struct
    from tests.test_rtl import ROOT
    from host.protocol import encode,FrameParser,INFO,STATUS,BEGIN_RUNS,RUN,COMMIT,TRIGGER
    commands=[(INFO,b''),(BEGIN_RUNS,struct.pack('<HHHHIBB',512,512,2,1,123,0,1)),
              (RUN,struct.pack('<HHI',0,0,8)),(RUN,struct.pack('<HHI',1,1023,4)),
              (COMMIT,b''),(ARM,b''),(TRIGGER,b''),(STATUS,b''),(ABORT,b''),(STATUS,b''),(INFO,b'')]
    requests=[encode(cmd,i+1,p) for i,(cmd,p) in enumerate(commands)]
    (ROOT/'build/sample_uart_vectors.hex').write_text('\n'.join(f"{int.from_bytes(f,'little'):064x}" for f in requests))
    run_tb('tb_sample_uart',['rtl/common/controller.v','rtl/common/gate_engine.v','rtl/common/sequence_engine.v','rtl/common/sample_engine.v','rtl/common/uart.v','rtl/common/packet_link.v'])
    responses=[int(line,16).to_bytes(32,'little') for line in (ROOT/'build/sample_uart_responses.hex').read_text().splitlines()]
    assert len(responses)==len(requests)
    for req,res in zip(requests,responses):
        assert FrameParser().feed(res)==[res]
        assert res[3]==req[3]|128 and res[4:6]==req[4:6] and res[6]==0
    status=responses[7][6:30]
    assert status[1]==3 and status[17]==1 and status[20]==1
    assert struct.unpack_from('<H',status,3)[0]==512
    assert struct.unpack_from('<H',status,5)[0]==1
