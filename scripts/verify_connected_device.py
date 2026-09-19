"""Opt-in live-board check. Uses small excursions around the DAC midpoint.

This exercises real UART/configuration logic, not analog output measurements.
Leaves both DACs at the midpoint, unarmed, and releases the serial port.
"""
import argparse
from dataclasses import asdict
from pathlib import Path
import datetime
import json
import sys
import time

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from host.protocol import Device,SerialTransport,ARM,TRIGGER,ABORT,DISARM
from host.model import Profile
from host.uart_settings import BAUD_RATES, DEFAULT_BAUD


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--port',required=True)
    parser.add_argument('--report',required=True)
    parser.add_argument('--baudrate',type=int,choices=BAUD_RATES,default=DEFAULT_BAUD,
                        help='UART rate matching the programmed FPGA firmware (default: 115200)')
    args=parser.parse_args()
    report={'date':datetime.datetime.now().isoformat(timespec='seconds'),'port':args.port,'baudrate':args.baudrate,
            'passed':False,'physical_output_measured':False,'controlled_external_edges_tested':False,'checks':[]}
    transport=None;device=None;started=False;failure=None
    try:
        transport=SerialTransport(args.port,args.baudrate);device=Device(transport)
        report['capabilities']=asdict(device.caps);report['initial_status']=device.status()
        if report['initial_status']['state'] not in (0,3):
            raise RuntimeError('Device already armed or active; existing experiment left unchanged')
        mid=1<<(device.caps.dac_bits-1)
        started=True
        for channel in range(device.caps.dac_count):
            for code in (mid-1,mid+1,mid):
                result=device.set_static(channel,code,mid)
                report['checks'].append({'test':'static','channel':channel,'requested_code':code,'status':result['status']})
            device.command(ABORT)
            status=device.status();assert status['state']==0 and status['code']==mid
        for channel,mode in enumerate(('ramp','function')):
            channel%=device.caps.dac_count
            p=Profile(name='Live UART verification',mode=mode,initial=mid,safe=mid,amplitude=mid-4,
                      dac_bits=device.caps.dac_bits,channel=channel,rise_ms=80,hold_ms=120,fall_ms=80,
                      trigger='software',repeat=2)
            identity=device.upload(p);device.command(ARM)
            status=device.status();assert status['state']==1 and status['config_id']==identity and status['code']==mid
            device.command(TRIGGER);began=time.monotonic();trace=[]
            while True:
                status=device.status();trace.append({'elapsed_s':time.monotonic()-began,**status})
                assert status['config_id']==identity and status['channel']==channel
                assert mid-4<=status['code']<=mid
                if status['state']==3:break
                if time.monotonic()-began>3:raise TimeoutError('Waveform did not complete')
                time.sleep(.005)
            assert status['loops']==2 and status['code']==mid
            assert any(s['state']==2 and s['code']==mid-4 for s in trace)
            assert any(s['state']==2 and mid-4<s['code']<mid for s in trace)
            report['checks'].append({'test':mode,'channel':channel,'repeat':2,'expected_duration_ms':560,'trace':trace})
        for source in range(device.caps.trigger_count):
            p=Profile(name='Live gate configuration verification',mode='ramp',timing_mode='gate',initial=mid,safe=mid,
                      dac_bits=device.caps.dac_bits,amplitude=mid-4,rise_ms=80,fall_ms=80,trigger_input=source)
            identity=device.upload(p);device.command(ARM);status=device.status()
            assert status['valid'] and status['gate_mode'] and status['trigger_input']==source and status['config_id']==identity
            assert status['state'] in (1,2)
            # No controlled external pulse generator is attached to this test.
            # If a real edge arrives, stop rather than leaving the hardware active.
            device.command(DISARM if status['state']==1 else ABORT)
            ended=device.status();assert ended['state']==0 and ended['code']==mid
            report['checks'].append({'test':'gate_upload_arm_cancel','source':source,'armed_status':status,'end_status':ended})
        report['passed']=True
    except Exception as exc:
        failure=exc;report['error']=str(exc)
    finally:
        if device is not None and started:
            try:
                device.command(ABORT)
                report['final_status']=device.set_static(0,mid,mid)['status']
                assert report['final_status']['state']==0 and report['final_status']['code']==mid
                report['restored_midpoint_unarmed']=True
            except Exception as exc:
                report['cleanup_error']=str(exc);report['passed']=False
        if transport is not None:transport.close()
        path=Path(args.report);path.parent.mkdir(parents=True,exist_ok=True)
        path.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({k:report.get(k) for k in ('port','baudrate','passed','error','capabilities','final_status','restored_midpoint_unarmed')},ensure_ascii=False,indent=2))
    if failure:print(type(failure).__name__,str(failure),file=sys.stderr)
    return 0 if report['passed'] else 1


if __name__=='__main__':raise SystemExit(main())
