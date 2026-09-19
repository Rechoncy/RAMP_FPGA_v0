"""Live four-DAC UART verification. Small codes around midpoint, no analog claims."""
import argparse
from dataclasses import asdict
import datetime,json,sys,time
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from host.protocol import Device,SerialTransport,ARM,TRIGGER,ABORT
from host.model import Profile
from host.uart_settings import BAUD_RATES, DEFAULT_BAUD


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--port',default='COM3');parser.add_argument('--report',default='build/parallel4/uart_validation.json')
    parser.add_argument('--baudrate',type=int,choices=BAUD_RATES,default=DEFAULT_BAUD,
                        help='UART rate matching the programmed FPGA firmware (default: 115200)')
    args=parser.parse_args()
    report={'timestamp':datetime.datetime.now().isoformat(),'port':args.port,'baudrate':args.baudrate,'passed':False,'physical_output_measured':False,'controlled_external_edges_tested':False,'checks':[]}
    transport=None;device=None;started=False
    try:
        transport=SerialTransport(args.port,args.baudrate);device=Device(transport);report['caps']=asdict(device.caps)
        assert device.caps.parallel_channels and device.caps.dac_count==4 and device.caps.core_hz==200000000 and device.caps.dac_divider==1
        initial=device.status_all();report['initial_status']=initial
        if any(s['state'] in (1,2) for s in initial):raise RuntimeError('An experiment is already armed/running; left unchanged')
        started=True;mid=1<<(device.caps.dac_bits-1)
        for ch in range(4):device.set_static(ch,mid-2+ch,mid)
        states=device.status_all();assert [s['code'] for s in states]==list(range(mid-2,mid+2))
        report['checks'].append({'test':'four_distinct_static_codes','status':states})
        ids=[]
        for ch in range(4):
            p=Profile(name=f'Parallel live DAC {ch+1}',mode='function' if ch%2 else 'ramp',channel=ch,
                      dac_bits=device.caps.dac_bits,initial=mid,safe=mid,amplitude=mid-4-ch,
                      rise_ms=1,hold_ms=4,fall_ms=1,repeat=500,trigger='software')
            ids.append(device.upload(p))
        device.group_command(ARM,list(range(4)));armed=device.status_all()
        assert all(s['state']==1 and s['config_id']==ids[i] for i,s in enumerate(armed))
        device.group_command(TRIGGER,list(range(4)));began=time.monotonic();trace=[]
        while True:
            states=device.status_all();trace.append({'elapsed_s':time.monotonic()-began,'status':states})
            for i,s in enumerate(states):assert s['config_id']==ids[i] and mid-4-i<=s['code']<=mid
            if all(s['state']==3 for s in states):break
            if time.monotonic()-began>6:raise TimeoutError('Parallel waveforms did not complete')
        assert any(all(s['state']==2 for s in batch['status']) for batch in trace)
        assert all(s['loops']==500 and s['code']==mid for s in states)
        report['checks'].append({'test':'four_parallel_ramp_gaussian_500_repeats','trace':trace})
        device.group_command(ARM,list(range(4)));device.group_command(TRIGGER,list(range(4)))
        device.channel_command(ABORT,2);device.set_static(2,mid+2,mid)
        states=device.status_all()
        assert states[2]['code']==mid+2 and states[2]['state']==0
        assert all(states[i]['state']==2 and states[i]['config_id']==ids[i] for i in (0,1,3))
        report['checks'].append({'test':'replace_DAC3_while_other_three_keep_playing','status':states})
        report['passed']=True
    except Exception as exc:
        report['error']=f'{type(exc).__name__}: {exc}'
    finally:
        if device and started:
            try:
                device.group_command(ABORT,list(range(4)))
                for ch in range(4):device.set_static(ch,mid,mid)
                report['final_status']=device.status_all()
                assert all(s['state']==0 and s['code']==mid for s in report['final_status'])
                report['restored_all_midpoint_unarmed']=True
            except Exception as exc:report['cleanup_error']=str(exc);report['passed']=False
        if transport:transport.close()
        path=Path(args.report);path.parent.mkdir(parents=True,exist_ok=True);path.write_text(json.dumps(report,indent=2),encoding='utf-8')
    print(json.dumps({k:v for k,v in report.items() if k not in ('checks','initial_status','final_status')},indent=2))
    return 0 if report['passed'] else 1


if __name__=='__main__':raise SystemExit(main())
