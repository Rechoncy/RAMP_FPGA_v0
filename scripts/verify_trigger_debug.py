"""Explicit hardware digital verification. Replaces RAM plans; never writes Flash.

Uses codes 504..520 and ends at 512, unarmed, with physical trigger routing.
Requires a temporary BIT that advertises virtual-trigger support.
"""
from pathlib import Path
import argparse,json,time,sys,traceback
from dataclasses import asdict
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from host.protocol import Device,SerialTransport,ARM
from host.usb_transport import USBTransport
from host.model import Profile


def run(device,report):
    checks=report['checks'];channels=list(range(device.caps.dac_count));hz=device.caps.core_hz
    def check(name,ok,detail=None):
        checks.append(dict(name=name,passed=bool(ok),detail=detail))
        print(('PASS ' if ok else 'FAIL ')+name,flush=True)
        if not ok:raise AssertionError(name)
    def wait(name,predicate,timeout=3):
        end=time.monotonic()+timeout
        while True:
            debug=device.trigger_debug();ss=device.status_all()
            if predicate(ss):
                check(name,True,dict(debug=debug,channels=ss));return ss
            if time.monotonic()>end:raise TimeoutError(name+': '+str(ss))
            time.sleep(.005)
    def action(op,source=0,high=.001,period=.1,count=1):
        return device.trigger_debug(op,source,round(high*hz),round(period*hz),count)
    def stop():
        device.trigger_debug('stop')
        check('Stopped, physical inputs restored, all DACs at abort code',
              device.trigger_debug()['mask']==0 and all(s['state']==0 and s['code']==512 for s in device.status_all()))
    check('Real firmware advertises trigger-debug capability',device.caps.trigger_debug,asdict(device.caps))
    check('Expected four 10-bit channels at 125 MHz',(device.caps.dac_count,device.caps.dac_bits,hz)==(4,10,125000000))
    try:
        device.trigger_debug('stop')
        for c in channels:device.set_static(c,512,512)
        for playback in ('hold','linear'):
            waves=[Profile(name='Virtual gate '+playback,mode='ramp',channel=c,initial=512,safe=512,
                           amplitude=(504,508,516,520)[c],rise_ms=20,fall_ms=20,
                           timing_mode='gate',trigger='external',trigger_input=0,playback=playback) for c in channels]
            for wave in waves:device.upload(wave)
            action('enable');device.group_command(ARM,channels)
            wait('All four gate engines ready '+playback,lambda ss:all(s['state']==1 and s['prepared'] for s in ss))
            try:action('enable',1)
            except RuntimeError:check('Changing source while armed is rejected',True)
            else:check('Changing source while armed is rejected',False)
            action('high')
            wait('Shared virtual input reaches all four targets '+playback,lambda ss:all(s['state']==2 and s['code']==w.amplitude for s,w in zip(ss,waves)))
            first=action('high');check('Repeated high does not synthesize another rising edge',first['emitted']==1)
            action('low')
            wait('Falling edge returns and automatically rearms '+playback,lambda ss:all(s['state']==1 and s['code']==512 and s['loops']>=1 for s in ss))
            action('pulse',high=.001)
            wait('Early falling edge returns and rearms '+playback,lambda ss:all(s['state']==1 and s['code']==512 and s['loops']>=2 for s in ss))
            stop()
        for c in channels:
            device.upload(Profile(mode='ramp',channel=c,initial=512,safe=512,amplitude=520,
                                  rise_ms=5,hold_ms=20,fall_ms=5,repeat=2,trigger='external'))
        action('enable');device.group_command(ARM,channels)
        wait('Fixed waveform armed',lambda ss:all(s['state']==1 for s in ss))
        action('periodic',high=.0001,period=.001,count=20)
        wait('Finite wave repeats and busy edges are counted',lambda ss:all(s['state']==1 and s['loops']==2 and s['ignored']>0 for s in ss))
        check('Exactly 20 generated pulses',device.trigger_debug()['emitted']==20)
        stop()
        for c in channels:
            device.upload(Profile(mode='scan',channel=c,initial=512,safe=512,scan_start=504+c,
                                  scan_step=2,scan_points=5,trigger_input=c%2))
        action('enable_all');device.group_command(ARM,channels)
        action('pulse',0,high=.000001)
        check('Input 1 advances only DAC 1 and DAC 3',[s['code'] for s in device.status_all()]==[504,512,506,512])
        action('pulse',1,high=.000001)
        check('Input 2 advances DAC 2 and DAC 4',[s['code'] for s in device.status_all()]==[504,505,506,507])
        action('periodic',0,high=.00001,period=.001,count=4)
        wait('Scan reaches final point from periodic pulses',lambda ss:ss[0]['state']==3 and ss[0]['code']==512 and ss[2]['code']==514)
        stop()
        wave=Profile(name='Event pulse',mode='ramp',initial=512,safe=512,amplitude=520,rise_ms=2,hold_ms=20,fall_ms=2)
        gate=Profile(name='Event gate',mode='ramp',initial=512,safe=512,amplitude=504,rise_ms=2,fall_ms=2,timing_mode='gate',playback='linear')
        seq=Profile(mode='sequence',sequence_mode='events',initial=512,safe=512,sequence_steps=[
            dict(kind='edge',input=0,edge='rising',repeat=1,waveform=wave.to_dict()),
            dict(kind='gate',input=1,edge='rising',repeat=1,waveform=gate.to_dict())])
        device.upload(seq);action('enable_all');device.channel_command(ARM,0)
        wait('Event program ready',lambda ss:ss[0]['prepared'] and ss[0]['state']==1)
        action('pulse',0,high=.000001)
        wait('Event advances to input 2 gate',lambda ss:ss[0]['sequence_step']==1 and ss[0]['prepared'] and ss[0]['state']==1)
        action('high',1)
        wait('Event gate holding',lambda ss:ss[0]['state']==2 and ss[0]['code']==504)
        action('low',1)
        wait('Event program wraps and waits for input 1',lambda ss:ss[0]['sequence_step']==0 and ss[0]['loops']==1 and ss[0]['state']==1)
        stop()
        action('enable_all');action('periodic',0,high=.001,period=.002,count=0)
        time.sleep(.03);debug=device.trigger_debug()
        check('Infinite pulse train runs until stopped',debug['running'] and debug['emitted']>=10,debug)
        stop()
        device.upload(gate);action('enable');device.channel_command(ARM,0);action('high')
        wait('Gate high before heartbeat loss',lambda ss:ss[0]['state']==2 and ss[0]['code']==504)
        # No heartbeat or status traffic for longer than the FPGA's 3 s deadline.
        time.sleep(3.3)
        debug=device.trigger_debug();ss=device.status_all()
        check('Autonomous watchdog abort and physical-source restoration',debug['expired'] and debug['mask']==0 and all(s['state']==0 and s['code']==512 for s in ss),dict(debug=debug,channels=ss))
    finally:
        device.trigger_debug('stop')
        for c in channels:device.set_static(c,512,512)
        report['final']=dict(debug=device.trigger_debug(),channels=device.status_all())


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--port',required=True)
    parser.add_argument('--baudrate',type=int,default=1000000);parser.add_argument('--output',required=True)
    parser.add_argument('--execute-hardware',action='store_true');args=parser.parse_args()
    if not args.execute_hardware:parser.error('Explicit --execute-hardware is required')
    report=dict(port=args.port,checks=[],passed=False);device=None
    try:
        transport=USBTransport(args.port) if args.port.startswith('usb:') else SerialTransport(args.port,args.baudrate)
        device=Device(transport,dac_model='3PD5651E');run(device,report);report['passed']=True
    except Exception:
        report['error']=traceback.format_exc();raise
    finally:
        if device:device.close()
        Path(args.output).write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')

if __name__=='__main__':main()
