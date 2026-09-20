from pathlib import Path
import subprocess
import pytest
from host.protocol import Device, ARM, ABORT
from host.parallel_simulator import ParallelSimulator

def test_trigger_generator_rtl(tmp_path):
    out=tmp_path/'debug.vvp'
    subprocess.run(['iverilog','-g2012','-s','tb_trigger_debug','-o',str(out),
                    'rtl/common/trigger_debug.v','tests/rtl/tb_trigger_debug.v'],check=True,capture_output=True)
    r=subprocess.run(['vvp',str(out)],capture_output=True,text=True,timeout=30)
    assert r.returncode==0,r.stdout+r.stderr
    assert 'PASS' in r.stdout

def test_old_firmware_rejects_debug():
    from host.simulator import Simulator
    device=Device(Simulator())
    with pytest.raises(ValueError,match='不支持真实触发调试'):device.trigger_debug('enable')

def test_worker_disconnect_releases_link_even_if_stop_ack_is_lost():
    from host.worker import DeviceWorker
    class LostDevice:
        debug_active=True
        closed=False
        def trigger_debug(self,op):raise TimeoutError('cable removed')
        def close(self):self.closed=True
    w=DeviceWorker();device=LostDevice();w.device=device;result=[];notice=[]
    w.result.connect(lambda op,data:result.append(op));w.progress.connect(notice.append)
    w.execute('disconnect',None)
    assert device.closed and w.device is None and result==['disconnect']
    assert any('3 秒' in s for s in notice)

@pytest.mark.parametrize('link',['usb','uart'])
def test_wire_to_scan_debug_chain(tmp_path,link):
    import struct
    from host.protocol import encode,FrameParser,INFO,SELECT_CHANNEL,BEGIN_SCAN,COMMIT,GROUP_COMMAND,DEBUG_CONFIG,DEBUG_ACTION,DEBUG_STATUS,DEBUG_STOP,STATUS
    from host.scan import SCAN_FORMAT
    cmds=[(INFO,b'')]
    for ch in range(4):
        cmds.extend([(SELECT_CHANNEL,bytes([ch])),(BEGIN_SCAN,struct.pack(SCAN_FORMAT,512,512,100+ch,10,5,123+ch,ch,ch%2,1)),(COMMIT,b'')])
    cmds.extend([(SELECT_CHANNEL,b'\0'),(DEBUG_CONFIG,b'\3'),(GROUP_COMMAND,bytes([ARM,15])),
                 (DEBUG_ACTION,struct.pack('<BBIII',0,2,100,200,1)),(STATUS,b''),
                 (DEBUG_ACTION,struct.pack('<BBIII',0,2,100,200,1)),(STATUS,b''),
                 (DEBUG_ACTION,struct.pack('<BBIII',0,3,50,100,3)),(STATUS,b''),(DEBUG_STATUS,b'')])
    # Exact retransmission must not produce another pulse.
    frames=[encode(cmd,i,p) for i,(cmd,p) in enumerate(cmds)]
    frames.insert(17,frames[16]);cmds.insert(17,cmds[16])
    cmds.append((DEBUG_STOP,b''));frames.append(encode(DEBUG_STOP,99))
    (tmp_path/'parallel_requests.hex').write_text('\n'.join(f'{int.from_bytes(f,"little"):064x}' for f in frames))
    root=Path.cwd();top='tb_usb_parallel' if link=='usb' else 'tb_trigger_uart'
    bench=(root/f'tests/rtl/{top}.v').read_text()
    if link=='usb':bench=bench.replace("codes[9:0]!==512 || codes[19:10]!==512 || codes[29:20]!==77 || codes[39:30]!==512","codes!=={4{10'd512}}")
    path=tmp_path/'tb.v';path.write_text(bench);out=tmp_path/'wire.vvp'
    subprocess.run(['iverilog','-g2012','-s',top,f'-P{top}.COUNT={len(frames)}','-o',str(out),
                    *map(str,(root/'rtl/common').glob('*.v')),str(path)],check=True,capture_output=True)
    r=subprocess.run(['vvp',str(out)],cwd=tmp_path,capture_output=True,text=True,timeout=120)
    assert r.returncode==0,r.stdout+r.stderr
    replies=FrameParser().feed(b''.join(int(s,16).to_bytes(32,'little') for s in (tmp_path/'parallel_responses.hex').read_text().splitlines()))
    assert len(replies)==len(frames)
    assert all(r[6]==0 and r[4:6]==f[4:6] and r[3]==f[3]|128 for r,f in zip(replies,frames))
    codes=[struct.unpack_from('<H',r,9)[0] for r,(cmd,_) in zip(replies,cmds) if cmd==STATUS]
    assert codes==[100,110,140]
    status=next(r for r,(cmd,_) in zip(replies,cmds) if cmd==DEBUG_STATUS)
    assert struct.unpack_from('<I',status,11)[0]==5

def test_trigger_page_commands_and_stale_state():
    import os
    os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
    from PySide6.QtWidgets import QApplication
    from host.trigger_debug_page import TriggerDebugPage
    from host.model import Capabilities
    app=QApplication.instance() or QApplication([])
    p=TriggerDebugPage();requests=[];p.requested.connect(requests.append)
    p.set_context(Capabilities(core_hz=125000000,trigger_count=2,trigger_debug=True),True,False)
    p.enable_all.click();assert requests[-1]['operation']=='enable_all'
    p.readback(dict(mask=3,levels=0,running=False,emitted=0));p.high.setValue(.001);p.period.setValue(.002);p.count.setValue(0)
    p.send('periodic');assert requests[-1]==dict(operation='periodic',input_index=0,high_ticks=125,period_ticks=250,count=0)
    p.readback(dict(mask=3,levels=1,running=True,emitted=1));assert all(not b.isEnabled() for b in p.actions)
    p.set_context(p.caps,False,False);assert not p.value and not p.stop.isEnabled()

def test_debug_tab_navigation_and_parameter_restore(tmp_path):
    import os
    os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
    from PySide6.QtWidgets import QApplication
    from host.app import Studio
    from host.storage import Library
    app=QApplication.instance() or QApplication([])
    w=Studio(Library(tmp_path));p=w.trigger_debug_page
    p.source.setCurrentIndex(1);p.high.setValue(.125);p.high.setUnit('µs',preserve_duration=True)
    p.period.setValue(1.25);p.count.setValue(0);p.value=dict(mask=3,levels=1,owned=True)
    w.debug_tabs.setCurrentIndex(1);w.open_debug(0)
    assert w.debug_tabs.currentIndex()==0 and w.stack.currentIndex()==3
    w.close();app.processEvents()
    w=Studio(Library(tmp_path));p=w.trigger_debug_page
    try:
        assert p.source.currentData()==1 and p.high.value()==.125 and p.high.unit.currentData()=='µs'
        assert p.period.value()==1.25 and p.count.value()==0
        assert not p.value and not w.connected
    finally:w.close();app.processEvents()

@pytest.mark.parametrize('bits',[10,14])
def test_virtual_gate_full_controller(tmp_path,bits):
    out=tmp_path/'parallel.vvp'
    subprocess.run(['iverilog','-g2012','-s','tb_virtual_parallel',f'-Ptb_virtual_parallel.BITS={bits}','-o',str(out),
                    *map(str,Path('rtl/common').glob('*.v')),'tests/rtl/tb_virtual_parallel.v'],check=True,capture_output=True)
    r=subprocess.run(['vvp',str(out)],capture_output=True,text=True,timeout=30)
    assert r.returncode==0,r.stdout+r.stderr
    assert 'PASS' in r.stdout
