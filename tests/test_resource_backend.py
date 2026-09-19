"""Actual INFO limits, exact RLE accounting and no-write failure boundaries."""
from dataclasses import FrozenInstanceError, replace
import json

import pytest
from PySide6.QtWidgets import QApplication

from host.functions import compile_samples
from host.model import Capabilities, Profile, fingerprint
from host.output_prepare import prepare_outputs
from host.parallel_simulator import ParallelSimulator
from host.protocol import Device, INFO, SELECT_CHANNEL, STATUS, encode
from host.resource_usage import assess_resources
from host.worker import DeviceWorker


class RecordingTransport(ParallelSimulator):
    def __init__(self, dacs=4, bits=10):
        super().__init__(dacs, 2, bits)
        self.commands = []
        self.max_steps = 64
        self.info_failure = None

    def exchange(self, frame):
        self.commands.append(frame[3])
        if frame[3] == INFO and self.info_failure:
            raise self.info_failure
        reply = super().exchange(frame)
        if frame[3] == INFO:
            payload = bytearray(reply[6:30])
            payload[18] = self.max_steps
            return encode(reply[3], int.from_bytes(reply[4:6], 'little'), payload)
        return reply


def plan(channel=0, **changes):
    return replace(Profile(mode='ramp', initial=512, safe=512, amplitude=500,
                           rise_ms=.001, hold_ms=.001, fall_ms=.001,
                           channel=channel), **changes)


def event_plan(steps=3):
    waveform = plan()
    return Profile(mode='sequence', initial=512, safe=512, sequence_mode='events',
                   sequence_steps=[dict(waveform=waveform.to_dict(), kind='edge',
                                        input=i % 2, edge='rising', delay_ms=0, repeat=1)
                                   for i in range(steps)])


@pytest.mark.parametrize('dacs,limit', [(1,8192), (2,4096), (4,2048), (8,1024)])
def test_refresh_reads_actual_per_dac_capacity_without_altering_outputs(dacs, limit):
    sim=RecordingTransport(dacs); device=Device(sim)
    assert device.caps.max_runs == limit and device.caps.board_id == 2
    device.set_static(0, 513, 512)
    before=device.status(0)
    sim.capacity=37; sim.max_steps=12; sim.commands.clear()
    caps=device.refresh_capabilities()
    assert sim.commands == [INFO]
    assert caps.max_runs == 37 and caps.sequence_steps == 12 and caps.dac_count == dacs
    assert device.status(0) == before


def test_exact_runs_separate_sample_count_and_equality_is_allowed():
    p=plan(); samples=compile_samples(p)
    used=len(samples.runs)
    assert used < samples.total
    caps=Capabilities(max_runs=used)
    report=assess_resources(p,caps)
    assert report.fits and report.required_runs == used and report.samples == 600
    assert report.max_runs == used and not report.error
    with pytest.raises(FrozenInstanceError):report.fits=False
    assert json.loads(json.dumps(report.report()))['required_runs'] == used
    rejected=assess_resources(p,replace(caps,max_runs=used-1))
    assert not rejected.fits and rejected.error_kind == 'capacity'
    assert rejected.required_runs == used and str(used) in rejected.error


def test_event_pool_deduplication_and_step_capacity_are_independent():
    p=event_plan(); caps=Capabilities(sequence_steps=64)
    report=assess_resources(p,caps)
    assert report.fits and report.required_steps == 3
    assert report.required_runs == len(compile_samples(plan()).runs)
    assert report.required_runs == len(compile_samples(p).runs)
    rejected=assess_resources(p,replace(caps,sequence_steps=2))
    assert not rejected.fits and rejected.error_kind == 'capacity'
    assert rejected.required_steps == 3 and rejected.max_steps == 2
    assert '事件步骤' in rejected.error


def test_native_scan_has_no_waveform_or_event_slots_and_checks_endpoints():
    p=plan(mode='scan', scan_start=512, scan_step=0, scan_points=65535)
    caps=Capabilities(max_runs=0,sequence_steps=0,trigger_scan=True)
    report=assess_resources(p,caps)
    assert report.fits and report.required_runs == 0 and report.required_steps == 0
    assert report.scan_points == 65535 and report.samples == 0
    assert '不占波形码段' in report.summary()
    invalid=assess_resources(replace(p,scan_step=1),caps)
    assert not invalid.fits and invalid.error_kind == 'invalid' and '末点' in invalid.error


def test_compiler_stop_and_invalid_function_do_not_invent_exact_size():
    p=plan(mode='function', function_layout='single', function_ms=.1,
           function='where((t*200000000)%2<.5,0,1023)')
    report=assess_resources(p,Capabilities(max_runs=2048))
    assert not report.fits and report.required_runs is None and report.samples is None
    assert report.error_kind == 'compiler_limit' and '软件编译器' in report.error
    invalid=assess_resources(replace(p,function='sqrt(-1)'),Capabilities())
    assert not invalid.fits and invalid.required_runs is None and invalid.error_kind == 'invalid'
    assert 'NaN' in invalid.error


def test_direct_upload_refreshes_and_rejects_without_writes():
    sim=RecordingTransport(); device=Device(sim); old=plan(amplitude=512)
    device.upload(old); before=device.status(0)
    assert device.caps.max_runs == 2048
    sim.capacity=2; sim.commands.clear(); reports=[]; refreshed=[]
    with pytest.raises(ValueError,match='连续码段'):
        device.upload(plan(),resources=reports.append,capabilities=refreshed.append)
    assert sim.commands == [INFO]
    assert reports[0][0].required_runs > 2 and not reports[0][0].fits
    assert refreshed[0].max_runs == 2
    assert device.status(0) == before


def test_prepare_checks_all_channels_before_any_begin_and_keeps_old_waveforms():
    sim=RecordingTransport(); device=Device(sim)
    for ch in (0,1):device.set_static(ch,512+ch,512)
    before=device.status_all(); sim.capacity=2; sim.commands.clear(); reports=[]
    with pytest.raises(ValueError,match='DAC 2'):
        prepare_outputs(device,dict(profiles=[plan(amplitude=512),plan(1)]),resources=reports.append)
    assert sim.commands == [INFO]
    assert len(reports[0]) == 2 and reports[0][0].fits and not reports[0][1].fits
    assert device.status_all() == before


def test_prepare_reuse_still_checks_fresh_limits_and_never_rearms_overflow():
    sim=RecordingTransport(); device=Device(sim); p=plan(); applied=[]
    prepare_outputs(device,dict(profiles=[p]),applied=applied.append)
    confirmed={0:(applied[0]['config_id'],fingerprint(p))}
    before=device.status(0); sim.capacity=2; sim.commands.clear()
    with pytest.raises(ValueError,match='资源检查'):
        prepare_outputs(device,dict(profiles=[p],confirmed=confirmed))
    assert sim.commands == [INFO]
    assert device.status(0) == before


def test_info_timeout_blocks_upload_without_using_stale_capacity():
    sim=RecordingTransport(); device=Device(sim);sim.commands.clear()
    sim.info_failure=TimeoutError('INFO read failed')
    with pytest.raises(TimeoutError,match='INFO'):
        device.upload(plan())
    assert sim.commands == [INFO]
    assert sim.channels[0].active is None and sim.channels[0].pending is None


@pytest.mark.parametrize('operation',['upload_many','prepare'])
def test_worker_emits_failure_resource_details_before_rejecting_batch(operation):
    app=QApplication.instance() or QApplication([])
    worker=DeviceWorker();sim=RecordingTransport();worker.device=Device(sim)
    sim.capacity=2;sim.commands.clear();reports=[];caps=[];failures=[]
    worker.resources.connect(reports.append);worker.capabilities.connect(caps.append)
    worker.failed.connect(lambda op,error:failures.append((op,error)))
    profiles=[plan(amplitude=512),plan(1)]
    argument=dict(profiles=profiles) if operation=='prepare' else profiles
    worker.execute(operation,argument)
    assert caps[-1].max_runs == 2 and len(reports[0]) == 2
    assert not reports[0][1].fits and failures[0][0] == operation
    assert set(sim.commands) <= {INFO,SELECT_CHANNEL,STATUS}
    assert all(c.active is None and c.pending is None for c in sim.channels)
    worker.disconnect()


def test_worker_manual_capability_refresh_is_read_only_and_offline_assessment_is_explicit():
    app=QApplication.instance() or QApplication([])
    worker=DeviceWorker();results=[];reports=[];worker.result.connect(lambda op,data:results.append((op,data)))
    worker.resources.connect(reports.append)
    offline=Capabilities(max_runs=3)
    worker.execute('resources',dict(profiles=[plan()],caps=offline))
    assert results[-1][0] == 'resources' and not reports[-1][0].fits
    sim=RecordingTransport();worker.device=Device(sim);sim.capacity=50;sim.commands.clear()
    worker.execute('capabilities',None)
    assert sim.commands == [INFO] and results[-1][0] == 'capabilities' and results[-1][1].max_runs == 50
    worker.execute('resources',dict(profiles=[plan()],caps=offline))
    assert reports[-1][0].fits and reports[-1][0].max_runs == 50
    assert sim.commands == [INFO,INFO]
    worker.disconnect()


def test_portable_resource_smoke_check_records_fresh_limit_and_last_channel_rejection():
    from host.portable_check import check_resource_preflight
    result=check_resource_preflight()
    assert len(result['checks'])==3 and result['commands_before_rejection']==['INFO']
    reports=result['reports']
    assert all(r['fits'] and r['required_runs']==1 for r in reports[:3])
    assert reports[3]['channel']==3 and not reports[3]['fits']
    assert reports[3]['required_runs']==9 and reports[3]['max_runs']==2
