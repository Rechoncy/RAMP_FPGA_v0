"""Explicit USB digital hardware diagnostics; no EEPROM/FPGA/Flash programming.

Example: python scripts/verify_usb_device.py --port usb:SERIAL
  --execute-hardware --output build/usb_check.json
Replaces temporary waveform configurations, uses raw codes 504..520, and ends
with all four channels idle at 512. Status readback is not an analog measurement.
"""
from __future__ import annotations
import argparse
from dataclasses import asdict
from datetime import datetime, timezone
import json
from pathlib import Path
import struct
import sys
import time
import traceback

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from host.model import Profile
from host.protocol import Device, INFO, BEGIN_RUNS, COMMIT, ARM, TRIGGER, ABORT, DISARM, encode
from host.usb_transport import USBTransport


def run(device, report, expected_core_mhz=200, info_windows=8):
    channels=list(range(4)); cleanup_allowed=False
    def check(name, ok, detail=None):
        report['checks'].append(dict(name=name,passed=bool(ok),detail=detail))
        if not ok:raise AssertionError(name)
    def states(name):
        value=device.status_all();report['statuses'].append(dict(name=name,channels=value));return value
    def wait(name, predicate, timeout=2):
        deadline=time.monotonic()+timeout
        while True:
            value=states(name)
            if predicate(value):return value
            if time.monotonic()>deadline:raise TimeoutError(name)
            time.sleep(.003)
    try:
        caps=device.refresh_capabilities();report['capabilities']=asdict(caps)
        check(f'Expected four 10-bit DACs, two triggers, {expected_core_mhz} MHz, 2048 BRAM segments',
              (caps.dac_count,caps.dac_bits,caps.trigger_count,caps.core_hz,caps.max_runs)==(4,10,2,expected_core_mhz*1_000_000,2048))
        check('USB, batch and interpolation firmware capabilities',caps.usb_fifo and caps.run_batch and caps.interpolation)
        before=states('read-only preflight')
        check('Clock locked and no active/armed channel',all(s['locked'] and s['state'] in (0,3) for s in before))
        cleanup_allowed=True
        for channel in channels:device.set_static(channel,512,512)
        for channel,target in enumerate((504,508,516,520)):
            device.set_static(channel,target,512)
            value=states(f'static DAC {channel+1}')
            expected=(504,508,516,520)[:channel+1]+(512,)*(3-channel)
            check(f'Static output and DAC isolation {channel+1}',tuple(s['code'] for s in value)==expected)
        for channel in channels:device.set_static(channel,512,512)
        # 16 frames in each window, unique sequence and CRC checks in transport.
        start=time.perf_counter();count=0
        for _ in range(info_windows):
            packets=[]
            for _ in range(16):
                device.sequence=(device.sequence+1)&65535
                packets.append(encode(INFO,device.sequence))
            replies=device.transport.exchange_many(packets)
            check('16-frame INFO window',len(replies)==16 and all(r[6]==0 for r in replies))
            count+=len(replies)
        report['info_window_test']=dict(frames=count,seconds=time.perf_counter()-start)
        # Full pool is loaded but never armed: exercises windows and final tail.
        runs=[(504+(i%17),1+(i%5)) for i in range(caps.max_runs)]
        for channel in channels:
            device.select_channel(channel);identity=0x9171000+channel
            start=time.perf_counter()
            device.command(BEGIN_RUNS,struct.pack('<HHHHIBBBBB',512,512,len(runs),1,identity,0,channel,0,0,1))
            device.upload_runs(runs);device.command(COMMIT)
            elapsed=time.perf_counter()-start;status=device.status()
            check(f'2048-descriptor batch commit DAC {channel+1}',status['valid'] and status['config_id']==identity and status['state']==0)
            report['uploads'].append(dict(channel=channel,descriptors=len(runs),seconds=elapsed,
                descriptor_bytes=6*len(runs),descriptor_bytes_per_second=6*len(runs)/elapsed,
                includes_begin_commit=True,played=False))
        # Four positive/negative targets, both storage encodings, software group trigger.
        for playback in ('hold','linear'):
            waves=[Profile(name=f'USB diagnostic {playback} {c+1}',mode='ramp',channel=c,
                dac_bits=10,initial=512,safe=512,amplitude=(520,504,516,508)[c],
                rise_ms=60,hold_ms=120,fall_ms=60,repeat=2,trigger='software',playback=playback)
                for c in channels]
            for wave in waves:device.upload(wave)
            device.group_command(ARM,channels)
            wait('ready '+playback,lambda ss:all(s['state']==1 and s['prepared'] for s in ss))
            device.group_command(TRIGGER,channels)
            observed=set();deadline=time.monotonic()+3
            while True:
                value=states('playing '+playback)
                check('Returned digital codes in 504..520',all(504<=s['code']<=520 for s in value))
                for c,s in enumerate(value):
                    if s['state']==2 and s['code']==waves[c].amplitude:observed.add(c)
                if all(s['state']==3 and s['loops']==2 for s in value):break
                if time.monotonic()>deadline:raise TimeoutError('finite playback '+playback)
                time.sleep(.003)
            check('Targets observed on all channels '+playback,observed==set(channels),sorted(observed))
            check('Finite playback returns to 512 '+playback,all(s['code']==512 for s in value))
        wave=Profile(name='USB infinite',mode='ramp',initial=512,safe=512,amplitude=520,
            rise_ms=10,hold_ms=20,fall_ms=10,repeat=0,trigger='software',playback='linear')
        device.upload(wave);device.channel_command(ARM,0)
        wait('infinite ready',lambda ss:ss[0]['prepared'])
        device.channel_command(TRIGGER,0)
        value=wait('infinite running',lambda ss:ss[0]['state']==2 and ss[0]['loops']>=2)
        check('Infinite repeat flag',value[0]['infinite_repeat'])
        device.select_channel(0)
        packet=bytearray(encode(ABORT,(device.sequence+100)&65535));packet[-1]^=1
        device.transport._write_all(bytes(packet),time.monotonic()+device.transport.timeout)
        time.sleep(.01)
        check('Bad-CRC ABORT ignored during infinite playback',device.status(0)['state']==2)
        device.channel_command(ABORT,0)
        check('Abort to 512',device.status(0)['code']==512)
        for c in channels:
            gate=Profile(name='USB gate prepare',mode='ramp',initial=512,safe=512,amplitude=520,
                channel=c,trigger='external',trigger_input=c%2,timing_mode='gate',playback='linear',rise_ms=.07,fall_ms=.07)
            device.upload(gate);device.channel_command(ARM,c)
            value=wait('gate ready',lambda ss:ss[c]['prepared'] and ss[c]['state']==1)
            check(f'Gate routing DAC {c+1}',value[c]['gate_mode'] and value[c]['trigger_input']==c%2)
            device.channel_command(DISARM,c)
        report['passed']=True
    finally:
        if cleanup_allowed:
            for c in channels:
                try:
                    device.channel_command(ABORT,c)
                    result=device.set_static(c,512,512)
                    report['cleanup'].append(dict(channel=c,passed=True,status=result['status']))
                except Exception as exc:
                    report['cleanup'].append(dict(channel=c,passed=False,error=repr(exc)))
            report['cleanup_passed']=len(report['cleanup'])==4 and all(x['passed'] for x in report['cleanup'])
            report['passed']=report.get('passed',False) and report['cleanup_passed']
        else:report['cleanup_skipped']='Read-only capability/state preflight did not permit changes'


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port',required=True);parser.add_argument('--output',required=True,type=Path)
    parser.add_argument('--execute-hardware',action='store_true')
    parser.add_argument('--expected-core-mhz',type=int,choices=(100,125,200),default=200,
                        help='Expected existing firmware clock; never changes its clock or programs it')
    parser.add_argument('--info-windows',type=int,default=8,help='Number of 16-frame INFO windows (1..4096)')
    args=parser.parse_args()
    if not args.execute_hardware or not args.port.startswith('usb:'):parser.error('Requires --execute-hardware and explicit usb:SERIAL')
    if not 1<=args.info_windows<=4096:parser.error('--info-windows must be 1..4096')
    if args.output.exists():parser.error('Report already exists; use a new output path')
    report=dict(started_utc=datetime.now(timezone.utc).isoformat(),port=args.port,passed=False,
        checks=[],statuses=[],uploads=[],cleanup=[],analog_measurement=False,
        physical_timing_measurement=False,external_trigger_measurement=False,flash_write=False,
        expected_core_mhz=args.expected_core_mhz)
    transport=None
    try:
        # No retries in acceptance runs: an initial missing reply must be visible.
        transport=USBTransport(args.port,retries=1);device=Device(transport)
        run(device,report,args.expected_core_mhz,args.info_windows)
        if report.get('passed'):
            report['reconnections']=[]
            for _ in range(3):
                transport.close();transport=USBTransport(args.port,retries=1);device=Device(transport)
                value=device.status_all()
                ok=asdict(device.caps)==report['capabilities'] and all(s['state']==0 and s['code']==512 and s['locked'] for s in value)
                report['reconnections'].append(dict(passed=ok,channels=value))
                if not ok:raise AssertionError('USB reconnection changed capabilities or idle outputs')
    except Exception as exc:report.update(passed=False,error=repr(exc),traceback=traceback.format_exc())
    finally:
        if transport is not None:
            try:transport.close()
            except Exception as exc:report.update(passed=False,close_error=repr(exc))
        report['finished_utc']=datetime.now(timezone.utc).isoformat()
        args.output.parent.mkdir(parents=True,exist_ok=True)
        args.output.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({k:report[k] for k in ('passed','port')}|{'checks':len(report['checks']),'error':report.get('error')},ensure_ascii=False))
    return 0 if report['passed'] else 1


if __name__=='__main__':raise SystemExit(main())
