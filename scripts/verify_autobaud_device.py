"""Explicit live UART diagnostics, no JTAG or Flash writes.

Requires the automatic-baud 125 MSPS four-channel image to be loaded first.
Uses DAC raw codes 504..520 and finishes idle at 512 on all four channels.
Status is digital firmware readback, not an analog or pin-level measurement.
"""
from __future__ import annotations
import argparse,json,struct,sys,time,traceback
from dataclasses import asdict
from datetime import datetime,timezone
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from host.protocol import Device,SerialTransport,INFO,BEGIN_RUNS,COMMIT,ARM,TRIGGER,ABORT,encode
from host.model import Profile
from host.uart_settings import BAUD_RATES


class ObservedTransport(SerialTransport):
    """Use the production transport; make its otherwise hidden retries visible."""
    def __init__(self,port,baudrate):
        super().__init__(port,baudrate)
        self.attempts=self.commands=self.retries=self.trains=0
        self.timings=[]
        self.retry_events=[]
        original=self.serial.write
        def write(data):
            if len(data)==32:self.attempts+=1
            if data==b'\x55'*16:self.trains+=1
            return original(data)
        self.serial.write=write
    def exchange(self,packet):
        attempts=self.attempts;start=time.perf_counter()
        try:return super().exchange(packet)
        finally:
            retries=max(0,self.attempts-attempts-1)
            self.commands+=1;self.retries+=retries
            if retries:self.retry_events.append(dict(command=packet[3],sequence=int.from_bytes(packet[4:6],'little'),retries=retries))
            self.timings.append(time.perf_counter()-start)
    def summary(self):
        return dict(baudrate=self.baudrate,commands=self.commands,transmissions=self.attempts,
            retries=self.retries,training_sequences=self.trains,
            retry_events=self.retry_events,
            mean_roundtrip_ms=1000*sum(self.timings)/len(self.timings) if self.timings else None)


def run(port,report):
    device=transport=None;modified=False;connections=[]
    def record(name,ok,detail=None):
        report['checks'].append(dict(name=name,passed=bool(ok),detail=detail))
        if not ok:raise AssertionError(name)
    def close():
        nonlocal transport,device
        if transport is not None:
            connections.append(transport.summary());transport.close()
        device=transport=None
    def connect(baud):
        nonlocal transport,device
        close();transport=ObservedTransport(port,baud);device=Device(transport,dac_model='3PD5651E')
        caps=device.caps
        record(f'Capability handshake at {baud}',caps.uart_autobaud and caps.usb_fifo and
               (caps.core_hz,caps.dac_count,caps.dac_bits,caps.trigger_count,caps.max_runs)==(125000000,4,10,2,2048))
        report['capabilities']=asdict(caps)
        return device
    def wait(predicate,seconds=4):
        deadline=time.monotonic()+seconds
        while True:
            states=device.status_all()
            if predicate(states):return states
            if time.monotonic()>deadline:raise TimeoutError('Expected DAC state did not arrive')
            time.sleep(.003)
    try:
        connect(115200)
        before=device.status_all();report['initial_status']=before
        record('Preflight: locked and no active/armed experiment',all(s['locked'] and s['state'] in (0,3) for s in before))
        modified=True
        targets=[504,508,516,520]
        for c,value in enumerate(targets):device.set_static(c,value,512)
        # Every rate switch uses the same RAM image/configurations, no ARM or download.
        ids=[s['config_id'] for s in device.status_all()]
        rate_order=[1000000,9600,921600,19200,460800,38400,230400,57600,115200,1000000,115200]
        for baud in rate_order:
            connect(baud);start=time.perf_counter()
            count=128 if baud==1000000 else 24
            for _ in range(count):device.refresh_capabilities()
            states=device.status_all()
            record(f'Four static codes and IDs retained at {baud}',[s['code'] for s in states]==targets and
                [s['config_id'] for s in states]==ids and all(s['state']==0 and s['locked'] for s in states),states)
            record(f'No automatic retries at {baud}',transport.retries==0,transport.summary())
            report['rate_sessions'].append(dict(baudrate=baud,info_frames=count,seconds=time.perf_counter()-start))
            print(f'PASS baud={baud}: {count} INFO, four DAC values retained, retries={transport.retries}',flush=True)
        # Deliberate bad-CRC ABORT must be ignored. Valid INFO follows as usual.
        connect(1000000)
        for c in range(4):device.set_static(c,512,512)
        profiles=[Profile(name=f'Live UART DAC {c+1}',mode='function' if c%2 else 'ramp',channel=c,
            dac_bits=10,initial=512,safe=512,amplitude=targets[c],rise_ms=.07,hold_ms=1.86,fall_ms=.07,
            repeat=0,trigger='software',playback='linear' if c>=2 else 'hold') for c in range(4)]
        ids=[device.upload(p) for p in profiles]
        device.group_command(ARM,list(range(4)))
        wait(lambda ss:all(s['prepared'] and s['state']==1 for s in ss))
        device.group_command(TRIGGER,list(range(4)))
        baseline=wait(lambda ss:all(s['state']==2 and s['loops']>1 for s in ss))
        for baud in (9600,1000000,115200,921600,1000000):
            connect(baud)
            states=device.status_all()
            record(f'Four waveforms retained while retraining at {baud}',
                all(s['state']==2 and s['config_id']==ids[c] and 504<=s['code']<=520 and s['infinite_repeat']
                    and s['loops']>baseline[c]['loops'] for c,s in enumerate(states)),states)
            baseline=states
        device.select_channel(0)
        frame=bytearray(encode(ABORT,(device.sequence+100)&65535));frame[-1]^=1
        transport.serial.write(frame);transport.serial.flush();time.sleep(.01)
        states=device.status_all()
        record('Bad-CRC ABORT ignored; all DACs still running',all(s['state']==2 for s in states))
        device.group_command(ABORT,list(range(4)))
        record('Valid group ABORT returns all DACs to 512',all(s['state']==0 and s['code']==512 for s in device.status_all()))
        # Exercise each full BRAM pool at 1M without playing its one-tick runs.
        for c in range(4):
            device.select_channel(c);identity=0x917B000+c
            runs=[(504+i%17,1+i%5) for i in range(device.caps.max_runs)]
            start=time.perf_counter()
            device.command(BEGIN_RUNS,struct.pack('<HHHHIBBBBB',512,512,len(runs),1,identity,0,c,0,0,1))
            device.upload_runs(runs);device.command(COMMIT)
            elapsed=time.perf_counter()-start;status=device.status()
            record(f'Full 2048-segment pool committed on DAC {c+1}',status['valid'] and status['config_id']==identity and status['state']==0)
            report['uploads'].append(dict(channel=c,baudrate=1000000,segments=len(runs),seconds=elapsed,played=False))
        record('No retries in positive communication checks',all(c['retries']==0 for c in connections) and transport.retries==0)
        report['passed']=True
    finally:
        if modified:
            try:
                if device is None:connect(115200)
                device.group_command(ABORT,list(range(4)))
                for c in range(4):device.set_static(c,512,512)
                states=device.status_all();report['final_status']=states
                report['restored_idle_512']=all(s['state']==0 and s['code']==512 for s in states)
                if not report['restored_idle_512']:report['passed']=False
            except Exception as exc:report.update(passed=False,cleanup_error=repr(exc))
        close();report['connections']=connections


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port',required=True);parser.add_argument('--output',required=True,type=Path)
    parser.add_argument('--execute-hardware',action='store_true');args=parser.parse_args()
    if not args.execute_hardware:parser.error('--execute-hardware is required')
    if args.output.exists():parser.error('Use a new output report path')
    report=dict(started_utc=datetime.now(timezone.utc).isoformat(),port=args.port,passed=False,
        checks=[],rate_sessions=[],uploads=[],analog_measurement=False,pin_level_measurement=False,
        external_trigger_measurement=False,flash_write=False)
    try:run(args.port,report)
    except Exception as exc:report.update(passed=False,error=repr(exc),traceback=traceback.format_exc())
    finally:
        report['finished_utc']=datetime.now(timezone.utc).isoformat()
        args.output.parent.mkdir(parents=True,exist_ok=True)
        args.output.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(dict(passed=report['passed'],checks=len(report['checks']),error=report.get('error')),ensure_ascii=False),flush=True)
    return int(not report['passed'])


if __name__=='__main__':raise SystemExit(main())
