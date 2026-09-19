import copy
import json
import struct
import tempfile
from pathlib import Path
import pytest
from host.model import Profile, Segment, Capabilities, compile_profile
from host.protocol import *
from host.simulator import Simulator
from host.storage import Library
from host.board import Board, generate_xdc, validate
from host.curves import envelope


def test_compiler_physical_duration_and_device_frequency():
    p=Profile()
    c=compile_profile(p)
    assert c.divider==200 and c.counts==(20000,100000,50000) and c.duration_ms==170
    other=compile_profile(p,Capabilities(core_hz=100_000_000))
    assert other.divider==100 and other.duration_ms==c.duration_ms


def test_200mhz_dac_alignment_and_legacy_capability_compatibility():
    p=Profile(update_us=.085)
    assert compile_profile(p).divider==17
    assert compile_profile(p,Capabilities(dac_divider=4)).divider==16
    dev=Device(Simulator())
    assert dev.caps.core_hz/dev.caps.dac_divider==200_000_000
    dev.upload(p)
    b=generate_xdc(Board())
    assert "CLKOUT0" in b and "CLKOUT1" not in b


@pytest.mark.parametrize("field,value",[("initial",1024),("repeat",0),("update_us",float('nan')),("safe",-1)])
def test_reject_invalid_parameters(field,value):
    p=Profile(); setattr(p,field,value)
    with pytest.raises(ValueError): compile_profile(p)


def test_short_segments_and_repeat_boundary():
    p=Profile(segments=[Segment(1023,.00008)],update_us=.08)
    assert compile_profile(p).counts==(1,)
    p.repeat=2
    with pytest.raises(ValueError,match="循环边界"): compile_profile(p)
    p.repeat=1; p.segments[0].duration_ms=1e-20
    with pytest.raises(ValueError): compile_profile(p)


def test_crc_known_vector_fragmentation_and_resync():
    assert crc(b"123456789")==0x29B1
    valid=encode(INFO,91)
    bad=bytearray(valid); bad[15]^=1
    parser=FrameParser(); result=[]
    for value in b"junk"+bad+b"x"+valid+valid:
        result+=parser.feed(bytes([value]))
    assert result==[valid,valid]


def test_demo_lifecycle_duplicate_trigger_and_immutable_upload():
    sim=Simulator(); dev=Device(sim)
    p=Profile(trigger="software",segments=[Segment(800,1000),Segment(100,1000)])
    identity=dev.upload(p)
    assert dev.status()["config_id"]==identity
    dev.command(ARM); assert dev.status()["state"]==1
    packet=encode(TRIGGER,54321)
    first=sim.exchange(packet); started=sim.started
    assert sim.exchange(packet)==first and sim.started==started
    with pytest.raises(RuntimeError): dev.upload(p)
    dev.command(ABORT); assert dev.status()["code"]==p.safe


def test_partial_upload_cannot_commit_and_does_not_replace_active():
    d=Device(Simulator()); p=Profile(); identity=d.upload(p)
    d.command(BEGIN,struct.pack("<HHHHIBxHHHH",100,200,2,1,123,1,100,0,1,1024))
    d.command(SEGMENT,struct.pack("<HHI",0,800,1000))
    with pytest.raises(RuntimeError): d.command(COMMIT)
    assert d.status()["config_id"]==identity


def test_profile_library_roundtrip_and_archive(tmp_path):
    lib=Library(tmp_path); p=lib.list()[0]; p.name="修改 / 不影响路径"
    lib.save(p); assert any(x.name==p.name for x in lib.list())
    lib.archive(p); assert not (lib.profiles/(p.id+".json")).exists()
    assert len(list((tmp_path/"archive").glob("*.json")))==1
    p.id="../bad"
    with pytest.raises(ValueError): lib.save(p)


def test_xdc_missing_pins_duplicate_and_time_constraints():
    b=Board(); draft=generate_xdc(b)
    assert "TODO: unassigned port uart_rx" in draft
    assert "period 20.000000" in draft
    with pytest.raises(ValueError,match="uart_rx"): generate_xdc(b,complete=True)
    b.pins["uart_rx"]="F13"
    with pytest.raises(ValueError,match="同时分配"): generate_xdc(b)
    b.pins["uart_rx"]="A1"; b.pins["uart_tx"]="A2" # syntax-only test, not real board assignments
    b.dac_delay_min=-1; b.dac_delay_max=2
    text=generate_xdc(b,complete=True)
    assert "-min -1.000000" in text and "-max 2.000000" in text


def test_xdc_rejects_tcl_injection_and_nonfinite():
    b=Board(); b.pins["uart_rx"]="A1}; exec evil"
    with pytest.raises(ValueError): generate_xdc(b)


@pytest.mark.parametrize("kind",["linear","smooth","exponential"])
def test_curve_templates_keep_duration_endpoints_and_monotonicity(kind):
    p=envelope(Profile(),kind,800,20,100,50)
    assert compile_profile(p).duration_ms==pytest.approx(170,abs=.05)
    assert p.segments[-1].target==p.initial
    peak_index=next(i for i,s in enumerate(p.segments) if s.target==800)
    assert all(a.target<=b.target for a,b in zip(p.segments[:peak_index],p.segments[1:peak_index+1]))
    assert len(p.segments)<=64
    b=Board(); b.dac_delay_min=float('nan')
    with pytest.raises(ValueError): generate_xdc(b)
