"""Sampling rate is firmware state, never a rescaling of saved experiments."""
from dataclasses import replace
import json
import numpy as np
import pytest
from host.board import Board, validate
from host.boot import generate_boot, identity
from host.extended_build import generate_top
from host.functions import compile_samples, shape_key, values, preview_samples
from host.interpolation import interpolate_codes
from host.model import Profile, offline_capabilities, compile_profile, fingerprint
from host.parallel_simulator import ParallelSimulator
from host.protocol import Device, ARM, ABORT
from host.resource_usage import assess_resources
from host.sample_rates import SAMPLE_RATES
from host.sequences import compile_sequence


def gaussian(**changes):
    return replace(Profile(mode='function',dac_bits=14,initial=5685,safe=5685,amplitude=0,
        timing_mode='gate',rise_ms=.07,fall_ms=.07,hold_ms=0,playback='linear',
        rise_function='I + (A-I)*(exp(-5*(1-t/T)**2)-exp(-5))/(1-exp(-5))',
        fall_function='I + (A-I)*(exp(-5*(t/T)**2)-exp(-5))/(1-exp(-5))'),**changes)


@pytest.mark.parametrize('hz', SAMPLE_RATES)
def test_gaussian_full_sample_accuracy_and_physical_duration(hz):
    p=gaussian();before=fingerprint(p)
    samples=compile_samples(p,max_runs=2048,sample_hz=hz)
    assert samples.total==round(.00014*hz)==samples.checked_samples
    assert samples.rise_ticks==samples.fall_ticks==round(.00007*hz)
    assert len(samples.runs)<=256
    start=p.initial;actual=[]
    for target,count in samples.runs:
        actual.extend(interpolate_codes(start,target,count,np.arange(count)));start=target
    expected=values(shape_key(p,hz),np.arange(samples.total),samples.total)[1].astype('int64')
    assert np.max(np.abs(np.array(actual)-expected))<=1
    assert start==p.initial
    assert compile_profile(p,offline_capabilities(core_hz=hz,dac_bits=14)).duration_ms==.14
    assert before==fingerprint(p)


@pytest.mark.parametrize('hz', SAMPLE_RATES)
def test_hold_compiler_preview_resource_and_rate_cache(hz):
    p=gaussian(dac_bits=10,initial=355,safe=355,playback='hold')
    data=compile_samples(p,sample_hz=hz)
    assert data.total==round(.00014*hz)
    for other in SAMPLE_RATES:
        assert compile_samples(p,sample_hz=other).total==round(.00014*other)
    assert compile_samples(p,sample_hz=hz)==data
    caps=offline_capabilities(core_hz=hz,dac_bits=10,dac_count=4,max_runs=2048)
    report=assess_resources(p,caps)
    assert report.fits and report.samples==data.total and report.required_runs==len(data.runs)
    t,_,_,total=preview_samples(p,hz)
    assert total==pytest.approx(.21) and max(t)<=total


@pytest.mark.parametrize('hz', SAMPLE_RATES)
def test_event_pool_reuse_and_delays_use_firmware_ticks(hz):
    wave=gaussian()
    p=Profile(mode='sequence',sequence_mode='events',dac_bits=14,initial=5685,safe=5685,
        sequence_steps=[dict(kind='gate',input=i,delay_ms=.003,waveform=wave.to_dict()) for i in (0,1)])
    data=compile_sequence(p,sample_hz=hz,max_runs=2048)
    assert len(data.runs)==len(compile_samples(wave,sample_hz=hz).runs)
    assert data.steps[0][4]==data.steps[1][4]==0
    assert all(step[8]==round(.00007*hz) and step[9]==round(.000003*hz) for step in data.steps)


@pytest.mark.parametrize('hz', SAMPLE_RATES)
def test_device_refresh_compiles_four_channels_and_static(hz):
    sim=ParallelSimulator(core_hz=hz);dev=Device(sim)
    assert dev.caps.core_hz==hz and dev.caps.max_runs==2048
    for channel in range(4):
        p=gaussian(dac_bits=10,initial=355,safe=355,channel=channel)
        dev.upload(p)
        assert sum(count for code,count in sim.channels[channel].active['segments'])==round(.00014*hz)
        dev.channel_command(ARM,channel)
        dev.channel_command(ABORT,channel)
        assert dev.set_static(channel,123,355)['code']==123


@pytest.mark.parametrize('hz', SAMPLE_RATES)
def test_boot_memory_and_clock_sources_share_selected_rate(tmp_path,hz):
    wave=gaussian(dac_bits=10,initial=355,safe=355)
    b=Board(core_mhz=hz/1e6,dac_count=4,boot_profiles={'0':wave.to_dict()})
    manifest=generate_boot(b,tmp_path/'boot')
    assert manifest[0]['samples']==round(.00014*hz)
    assert manifest[0]['sample_hz']==hz and manifest[0]['config_id']==identity(wave,hz)
    assert f'.CORE_HZ({hz})' in (tmp_path/'boot/ramp_boot_top.v').read_text()
    top=generate_top(replace(b,usb_fifo={'enabled':True}))
    assert f'.CORE_HZ({hz})' in top and f'.CLK_HZ({hz})' in top


def test_known_dac_rejects_overclock_and_wrong_width_before_output():
    assert validate(Board(dac_model='3PD5651E',core_mhz=200))
    assert validate(Board(dac_model='3PD5651E',core_mhz=125,dac_bits=14))
    assert not validate(Board(dac_model='3PD5651E',core_mhz=125))
    sim=ParallelSimulator();dev=Device(sim,dac_model='3PD5651E')
    with pytest.raises(ValueError,match='125 MSPS'):dev.upload(gaussian(dac_bits=10,initial=355,safe=355))
    with pytest.raises(ValueError,match='125 MSPS'):dev.set_static(0,123,512)
    with pytest.raises(ValueError,match='125 MSPS'):dev.channel_command(ARM,0)
    assert all(c.active is None and c.code==512 for c in sim.channels)
    dev.channel_command(ABORT,0)  # Stopping remains possible.
    assert len({identity(gaussian(),hz) for hz in SAMPLE_RATES})==3


def test_rate_controls_roundtrip_and_do_not_modify_waveform(tmp_path):
    from PySide6.QtWidgets import QApplication
    from host.board_page import BoardPage
    from host.storage import Library
    app=QApplication.instance() or QApplication([])
    page=BoardPage(Library(tmp_path));wave=gaussian();before=fingerprint(wave)
    page.output_rate.setCurrentIndex(page.output_rate.findData(125_000_000))
    page.dac_model.setCurrentIndex(page.dac_model.findData('3PD5651E'))
    board=page.collect();saved=Board.from_dict(json.loads(json.dumps(board.to_dict())))
    assert saved.core_mhz==125 and saved.dac_model=='3PD5651E'
    assert '8 ns' in page.clock_note.text() and '重新' in page.clock_note.text()
    assert before==fingerprint(wave)
    page.close();app.processEvents()
