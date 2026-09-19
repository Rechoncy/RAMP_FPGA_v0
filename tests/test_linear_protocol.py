"""Host protocol/boot contracts; these demo tests make no physical IO claims."""
from dataclasses import replace
import json
import struct

import pytest

from host.board import Board
from host.boot import capacity, generate_boot, identity
from host.functions import compile_samples
from host.model import Profile, fingerprint
from host.output_prepare import prepare_outputs
from host.parallel_simulator import ParallelSimulator
from host.protocol import (Device, INFO, STATUS, SELECT_CHANNEL, COMMIT, RUN,
    BEGIN_RUNS, BEGIN_LINEAR, BEGIN_GATE, BEGIN_LINEAR_GATE, BEGIN_SEQUENCE,
    BEGIN_SEQUENCE_LINEAR, WRITE_STEP, ARM, ABORT, TRIGGER, DISARM, encode)
from host.sequences import compile_sequence, pack_step
from tests.test_interpolation_compiler import gaussian, constant, sequence


class RecordingSimulator(ParallelSimulator):
    def __init__(self,bits=14,interpolation=True):
        super().__init__(4,2,bits,interpolation)
        self.commands=[];self.frames=[];self.info_error=None;self.info_patch=None

    def exchange(self,frame):
        self.commands.append(frame[3]);self.frames.append(frame)
        if frame[3]==INFO and self.info_error:raise self.info_error
        reply=super().exchange(frame)
        if frame[3]==INFO and self.info_patch:
            payload=bytearray(reply[6:30]);self.info_patch(payload)
            reply=encode(reply[3],int.from_bytes(reply[4:6],'little'),payload)
        return reply


@pytest.mark.parametrize('cmd,payload',[
    (BEGIN_LINEAR,struct.pack('<HHHHIBBBBB',355,355,2,1,10,1,0,0,0,1)),
    (BEGIN_LINEAR_GATE,struct.pack('<HHHHIBBHI',355,355,2,1,10,0,0,0,32)),
    (BEGIN_SEQUENCE_LINEAR,struct.pack('<HHHHIB',355,355,2,1,10,0)),
])
def test_old_firmware_explicitly_rejects_every_new_begin(cmd,payload):
    sim=RecordingSimulator(interpolation=False);dev=Device(sim)
    dev.set_static(0,355,355);old=sim.channels[0].active
    assert not dev.caps.interpolation
    with pytest.raises(RuntimeError,match='命令或版本不支持'):dev.command(cmd,payload)
    assert sim.channels[0].active is old and sim.channels[0].pending is None


@pytest.mark.parametrize('mutation', ['old','capacity','revision','min_ticks','timeout'])
def test_fresh_capability_failure_before_any_write(mutation):
    sim=RecordingSimulator();dev=Device(sim);dev.set_static(0,355,355)
    old=sim.channels[0].active
    assert dev.caps.interpolation and dev.caps.max_runs==2048
    if mutation=='old':
        for channel in sim.channels:channel.interpolation=False
    elif mutation=='capacity':sim.capacity=128
    elif mutation=='revision':sim.info_patch=lambda p:p.__setitem__(21,2)
    elif mutation=='min_ticks':sim.info_patch=lambda p:p.__setitem__(20,31)
    else:sim.info_error=TimeoutError('INFO timeout')
    sim.commands.clear();sim.frames.clear()
    with pytest.raises((ValueError,TimeoutError)):dev.upload(gaussian())
    assert sim.commands==[INFO]
    assert sim.channels[0].active is old and sim.channels[0].pending is None


def test_entire_selection_preflight_rejects_last_dac_before_any_change():
    sim=RecordingSimulator();dev=Device(sim)
    for i in range(4):dev.set_static(i,355+i,355)
    old=[c.active for c in sim.channels]
    sim.capacity=128;sim.commands.clear()
    flat=replace(gaussian(),amplitude=5685)
    plans=[replace(flat,channel=i) for i in range(3)]+[replace(gaussian(),channel=3)]
    reports=[]
    with pytest.raises(ValueError,match='DAC 4'):
        prepare_outputs(dev,dict(profiles=plans),resources=reports.append)
    assert sim.commands==[INFO]
    assert [c.active for c in sim.channels]==old
    assert [r.fits for r in reports[0]]==[True,True,True,False]


def test_four_channel_14bit_gaussian_protocol_routes_and_rearms(monkeypatch):
    now=[0.];monkeypatch.setattr('host.simulator.time.monotonic',lambda:now[0])
    sim=RecordingSimulator();dev=Device(sim)
    plans=[replace(gaussian(),channel=i,trigger_input=i%2) for i in range(4)]
    original=[fingerprint(p) for p in plans]
    result=prepare_outputs(dev,dict(profiles=plans))
    assert result['uploaded']==4 and dev.caps.dac_bits==14 and dev.caps.max_runs==2048
    assert sim.commands.count(BEGIN_LINEAR_GATE)==4
    assert [fingerprint(p) for p in plans]==original
    for i,c in enumerate(sim.channels):
        assert c.active['linear'] and c.active['gate'] and len(c.active['segments'])==139
        assert c.active['trigger_input']==i%2
    sim.external_trigger(0,'rising');now[0]=80e-6
    assert [s['code'] for s in dev.status_all()]==[0,5685,0,5685]
    now[0]=1.;assert [s['code'] for s in dev.status_all()]==[0,5685,0,5685]
    sim.external_trigger(0,'falling');now[0]+=.00008
    state=dev.status_all()
    assert all(s['state']==1 and s['code']==5685 and s['interpolation_mode'] and s['prepared'] for s in state)
    assert [s['loops'] for s in state]==[1,0,1,0]
    sim.external_trigger(1,'rising');now[0]+=.00008
    assert [s['code'] for s in dev.status_all()]==[5685,0,5685,0]
    dev.group_command(ABORT,[0,1,2,3]);assert all(s['state']==0 for s in dev.status_all())


def test_mixed_event_flags_reuse_actual_pool_and_fresh_compatibility():
    sim=RecordingSimulator(bits=10);sim.capacity=2;dev=Device(sim)
    p=sequence([constant('hold'),constant('linear'),constant('linear')])
    dev.upload(p);active=sim.channels[0].active
    assert active['program_linear'] and len(active['segments'])==2
    assert [s[4] for s in active['steps']]==[0,1,1]
    assert [s[11] for s in active['steps']]==[0,1,1]
    assert BEGIN_SEQUENCE_LINEAR in sim.commands and BEGIN_SEQUENCE not in sim.commands
    sim.capacity=1;sim.commands.clear()
    with pytest.raises(ValueError,match='容量'):dev.upload(p)
    assert sim.commands==[INFO] and sim.channels[0].active is active
    sim.capacity=2
    for c in sim.channels:c.interpolation=False
    sim.commands.clear()
    with pytest.raises(ValueError,match='不支持分段线性插值'):dev.upload(p)
    assert sim.commands==[INFO] and sim.channels[0].active is active


def test_old_sequence_begin_rejects_linear_step_flags():
    sim=RecordingSimulator(bits=10);dev=Device(sim)
    p=sequence([constant('linear')]);step=compile_sequence(p).steps[0]
    dev.command(BEGIN_SEQUENCE,struct.pack('<HHHHIB',512,512,1,1,10,0))
    with pytest.raises(RuntimeError,match='参数超出范围'):dev.command(WRITE_STEP,pack_step(step))
    assert sim.channels[0].active is None and not sim.channels[0].pending['steps']


@pytest.mark.parametrize('invalid',['rise_endpoint','final_endpoint','fall_duration','incomplete'])
def test_gate_commit_validation_keeps_prior_active_configuration(invalid):
    sim=RecordingSimulator();dev=Device(sim);dev.set_static(0,355,355)
    old=sim.channels[0].active
    dev.command(BEGIN_LINEAR_GATE,struct.pack('<HHHHIBBHI',355,355,2,1,10,0,0,0,32))
    dev.command(RUN,struct.pack('<HHI',0,1 if invalid=='rise_endpoint' else 0,32))
    if invalid!='incomplete':
        dev.command(RUN,struct.pack('<HHI',1,354 if invalid=='final_endpoint' else 355,
                                    33 if invalid=='fall_duration' else 32))
    with pytest.raises(RuntimeError,match='方案未完整下载' if invalid=='incomplete' else '参数超出范围'):
        dev.command(COMMIT)
    assert sim.channels[0].active is old and dev.status(0)['code']==355


def test_short_linear_run_rejected_while_hold_one_tick_is_compatible():
    sim=RecordingSimulator();dev=Device(sim)
    args=struct.pack('<HHHHIBBBBB',355,355,1,1,10,0,0,0,0,1)
    dev.command(BEGIN_LINEAR,args)
    with pytest.raises(RuntimeError,match='参数超出范围'):dev.command(RUN,struct.pack('<HHI',0,355,31))
    dev.command(BEGIN_RUNS,args);dev.command(RUN,struct.pack('<HHI',0,355,1));dev.command(COMMIT)
    assert not dev.status()['interpolation_mode'] and sim.channels[0].active['segments']==[(355,1)]


def test_program_commit_checks_only_linear_steps_and_keeps_old_bank():
    sim=RecordingSimulator();dev=Device(sim);dev.set_static(0,355,355)
    old=sim.channels[0].active
    dev.command(BEGIN_SEQUENCE_LINEAR,struct.pack('<HHHHIB',355,355,2,2,10,0))
    for i,linear in enumerate((0,1)):
        step=(i,0,0,0,i,1,0,0,1,0,1,linear)
        dev.command(WRITE_STEP,pack_step(step));dev.command(RUN,struct.pack('<HHI',i,355,1))
    with pytest.raises(RuntimeError,match='参数超出范围'):dev.command(COMMIT)
    assert sim.channels[0].active is old
    # The same short hold descriptor is valid in a mixed program when the
    # separately referenced linear descriptor meets the 32-tick minimum.
    dev.command(BEGIN_SEQUENCE_LINEAR,struct.pack('<HHHHIB',355,355,2,2,11,0))
    for i,linear in enumerate((0,1)):
        dev.command(WRITE_STEP,pack_step((i,0,0,0,i,1,0,0,1,0,1,linear)))
        dev.command(RUN,struct.pack('<HHI',i,355,32 if linear else 1))
    dev.command(COMMIT)
    assert sim.channels[0].active is not old


def boot_configs(directory):
    wrapper=(directory/'ramp_boot_top.v').read_text('ascii')
    return int(wrapper.split(".BOOT_CONFIGS(2048'h",1)[1].split(')',1)[0],16)


def test_four_dac_boot_configuration_bit7_and_46bit_dual_bank_capacity(tmp_path):
    plans=[replace(gaussian(),channel=i,trigger_input=i%2) for i in range(4)]
    b=Board(dac_count=4,dac_bits=14,boot_profiles={str(i):p.to_dict() for i,p in enumerate(plans)})
    out=tmp_path/'boot';manifest=generate_boot(b,out);packed=boot_configs(out)
    assert capacity(4)==2048 and len(manifest)==4
    for i,p in enumerate(plans):
        cfg=(packed>>(256*i))&((1<<256)-1)
        assert cfg&128 and cfg&2 and (cfg>>96)&65535==139
        assert (cfg>>112)&65535==70 and (cfg>>128)&0xffffffff==14000
        assert (cfg>>64)&0xffffffff==identity(p)
        rows=(out/f'boot_dac{i}.mem').read_text('ascii').splitlines()
        assert len(rows)==4096 and all(len(s)==12 for s in rows)
        words=[int(s,16) for s in rows]
        assert all(0<=w<(1<<46) for w in words)
        expected=compile_samples(p).runs
        assert [(w>>32,w&0xffffffff) for w in words[:139]]==list(expected)
        assert set(words[139:])=={0}
        assert manifest[i]['runs']==139 and manifest[i]['samples']==28000
        assert manifest[i]['playback']=='linear' and manifest[i]['max_error']==1
        assert manifest[i]['checked_samples']==28000


def test_boot_mixed_sequence_flags_and_old_mode_stay_distinct(tmp_path):
    event=sequence([constant('hold'),constant('linear'),constant('linear')])
    old=replace(constant('hold'),channel=1)
    b=Board(dac_count=4,boot_profiles={'0':event.to_dict(),'1':old.to_dict()})
    out=tmp_path/'boot';generate_boot(b,out);cfg=boot_configs(out)
    assert cfg&128 and cfg&16 and not (cfg>>(256+7))&1
    steps=[int(s,16).to_bytes(24,'little') for s in (out/'boot_dac0.mem.steps').read_text().splitlines()]
    assert len(steps)==128 and [struct.unpack('<BBBBHHHHIIHH',s)[-1] for s in steps[:3]]==[0,1,1]
    rows=(out/'boot_dac0.mem').read_text().splitlines();assert len(rows)==4096
    assert sum(int(s,16)!=0 for s in rows)==2
    assert json.loads((out/'boot_manifest.json').read_text('utf-8'))['channels'][1]['playback']=='hold'


def test_boot_cannot_sum_active_and_pending_banks_to_fit(tmp_path):
    waves=[replace(gaussian(),amplitude=i) for i in range(16)]
    required=sum(len(compile_samples(w).runs) for w in waves)
    assert 2048<required<4096
    p=Profile(mode='sequence',dac_bits=14,initial=5685,safe=5685,sequence_mode='events',
              sequence_steps=[dict(waveform=w.to_dict(),kind='gate',input=0,edge='rising',
                                   repeat=1,delay_ms=0) for w in waves])
    with pytest.raises(ValueError,match='容量'):
        generate_boot(Board(dac_count=4,dac_bits=14,boot_profiles={'0':p.to_dict()}),tmp_path/'too_small')


def test_legacy_hold_upload_uses_original_begin_and_code_stream():
    sim=RecordingSimulator(bits=10);dev=Device(sim)
    p=replace(gaussian(10),playback='hold');d=compile_samples(p)
    dev.upload(p)
    assert BEGIN_GATE in sim.commands and BEGIN_LINEAR_GATE not in sim.commands
    assert sim.channels[0].active['segments']==list(d.runs) and len(d.runs)==712
    assert not dev.status()['interpolation_mode']
