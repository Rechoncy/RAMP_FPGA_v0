"""Standalone waveform loops share the already released run-table protocol."""
import json
import struct
from dataclasses import replace

import pytest

from host.functions import compile_samples
from host.model import Capabilities, Profile, compile_profile, fingerprint
from host.parallel_simulator import ParallelSimulator
from host.protocol import Device, INFO, BEGIN_RUNS, ARM, TRIGGER, ABORT, encode
from host.resource_usage import assess_resources
from host.storage import Library


def waveform(shape='ramp', bits=10, trigger='software'):
    mid = 1 << (bits - 1)
    return Profile(name='无限单波形', mode='ramp' if shape == 'ramp' else 'function',
                   dac_bits=bits, initial=mid, safe=mid+2, amplitude=mid-8,
                   repeat=0, trigger=trigger, trigger_input=1, trigger_edge='falling',
                   rise_ms=.000020, hold_ms=.000010, fall_ms=.000020,
                   function_layout='single' if shape == 'single' else 'staged',
                   function='I - 8*sin(2*pi*t/T)', function_ms=.00005)


@pytest.mark.parametrize('bits', [10, 12, 14])
@pytest.mark.parametrize('shape', ['ramp', 'staged', 'single'])
def test_standalone_infinite_save_load_capacity_and_boot(tmp_path, bits, shape):
    from host.board import Board
    from host.boot import generate_boot

    p = waveform(shape, bits)
    caps = Capabilities(dac_bits=bits, infinite_repeat=True)
    library = Library(tmp_path/'library')
    library.save(p)
    restored = next(saved for saved in library.list() if saved.id == p.id)
    assert fingerprint(restored) == fingerprint(p)
    assert not library.errors
    data = compile_samples(p)
    usage = assess_resources(p, caps)
    for count in (1, 29, 65535):
        finite = replace(p, repeat=count)
        assert compile_profile(p, caps) == compile_profile(finite, caps)
        assert data == compile_samples(finite)
        finite_usage = assess_resources(finite, caps)
        assert usage.fits and usage.required_runs == finite_usage.required_runs
        assert usage.samples == finite_usage.samples
    out = tmp_path/'boot'
    manifest = generate_boot(Board(dac_bits=bits, dac_count=4,
                                   boot_profiles={'0': p.to_dict()}), out)
    assert manifest[0]['profile']['repeat'] == 0
    assert manifest[0]['runs'] == len(data.runs)
    assert manifest[0]['samples'] == data.total
    loaded = json.loads((out/'boot_manifest.json').read_text(encoding='utf-8'))
    assert loaded['channels'][0]['profile']['repeat'] == 0


@pytest.mark.parametrize('shape', ['ramp', 'staged', 'single'])
def test_old_firmware_rejects_standalone_loop_before_mutation(shape):
    class OldTransport(ParallelSimulator):
        def __init__(self):
            super().__init__()
            self.commands = []

        def exchange(self, frame):
            self.commands.append(frame[3])
            reply = super().exchange(frame)
            if frame[3] == INFO:
                payload = bytearray(reply[6:30])
                payload[17] &= ~64
                return encode(reply[3], int.from_bytes(reply[4:6], 'little'), payload)
            return reply

    sim = OldTransport()
    device = Device(sim)
    with pytest.raises(ValueError, match='无限重复需要新版 FPGA'):
        device.upload(waveform(shape))
    assert sim.commands == [INFO, INFO]
    assert all(c.active is None and c.pending is None for c in sim.channels)
    device.upload(replace(waveform(shape), repeat=29))
    assert device.status(0)['valid']


@pytest.mark.parametrize('shape', ['ramp', 'staged', 'single'])
@pytest.mark.parametrize('trigger', ['software', 'external'])
def test_standalone_infinite_demo_after_65535_rounds_and_abort(monkeypatch, shape, trigger):
    now = [1.]
    monkeypatch.setattr('host.simulator.time.monotonic', lambda: now[0])

    class RecordingTransport(ParallelSimulator):
        def __init__(self):
            super().__init__()
            self.begin_payloads = []

        def exchange(self, frame):
            if frame[3] == BEGIN_RUNS:
                self.begin_payloads.append(frame[6:30])
            return super().exchange(frame)

    p = waveform(shape, trigger=trigger)
    samples = compile_samples(p)
    gold = [v for v, count in samples.runs for _ in range(count)]
    sim = RecordingTransport()
    device = Device(sim)
    device.upload(p)
    assert len(sim.begin_payloads) == 1
    assert struct.unpack_from('<H', sim.begin_payloads[0], 6)[0] == 0
    device.channel_command(ARM, 0)
    if trigger == 'external':
        # The selected interface and falling edge remain effective in loop mode.
        sim.external_trigger(0, 'falling')
        sim.external_trigger(1, 'rising')
        assert device.status(0)['state'] == 1
        sim.external_trigger(1, 'falling')
    else:
        device.channel_command(TRIGGER, 0)
    for round_no in (0, 1, 65534, 65535, 65536, 1_000_000):
        for tick in (0, len(gold)//2, len(gold)-1):
            now[0] = 1 + (round_no*len(gold)+tick+.25)/200_000_000
            status = device.status(0)
            assert status['state'] == 2 and status['infinite_repeat']
            assert status['code'] == gold[tick]
            assert status['loops'] == min(round_no, 65535)
    if trigger == 'external':
        before = sim.channels[0].started
        sim.external_trigger(1, 'falling')
        assert device.status(0)['ignored'] == 1
        assert sim.channels[0].started == before
    device.channel_command(ABORT, 0)
    now[0] += 1
    sim.external_trigger(1, 'falling')
    status = device.status(0)
    assert status['state'] == 0 and status['code'] == p.safe

    # Changing back to any finite count must clear the previous infinite mode.
    for count in (1, 29, 65535):
        device.upload(replace(p, repeat=count))
        device.channel_command(ARM, 0)
        if trigger == 'external':
            sim.external_trigger(1, 'falling')
        else:
            device.channel_command(TRIGGER, 0)
        now[0] += (len(gold)*count+.25)/200_000_000
        status = device.status(0)
        assert status['state'] == (1 if trigger == 'external' else 3)
        assert status['code'] == p.initial and status['loops'] == count
        assert not status['infinite_repeat']
        device.channel_command(ABORT, 0)


@pytest.mark.parametrize('mode,timing', [('segments', 'fixed'), ('scan', 'fixed'),
                                       ('ramp', 'gate'), ('function', 'gate')])
def test_unsupported_modes_never_accept_zero_repeats(mode, timing):
    p = replace(waveform(), mode=mode, timing_mode=timing, trigger='external', trigger_edge='rising')
    caps = Capabilities(infinite_repeat=True, trigger_scan=True)
    with pytest.raises(ValueError, match='重复次数'):
        compile_profile(p, caps)


@pytest.mark.parametrize('count', [-1, 65536, True, 0.0])
def test_infinite_sentinel_still_requires_valid_integer(count):
    with pytest.raises(ValueError, match='重复次数'):
        compile_profile(replace(waveform(), repeat=count), Capabilities(infinite_repeat=True))
