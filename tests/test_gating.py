from dataclasses import replace
from pathlib import Path
import subprocess
import pytest
from host.model import Profile,Capabilities,compile_profile
from host.functions import compile_samples,preview_samples
from host.protocol import Device,ARM,ABORT,DISARM,BEGIN_GATE,INFO,encode
from host.simulator import Simulator


@pytest.mark.parametrize('bits',[10,12,14])
def test_gate_compile_and_rtl(bits):
    p=Profile(mode='ramp',timing_mode='gate',dac_bits=bits,initial=1<<(bits-1),safe=0,amplitude=0,rise_ms=.00005,hold_ms=1000,fall_ms=.000045)
    c=compile_profile(p,replace(Capabilities(),dac_bits=bits)); samples=compile_samples(p)
    assert samples.total==19 and sum(n for _,n in samples.runs[:samples.rise_runs])==10
    assert sum(n for _,n in samples.runs[samples.rise_runs:])==9
    assert c.duration_ms==pytest.approx(.000095)
    assert preview_samples(p)[3]>c.duration_ms
    root=Path(__file__).resolve().parents[1]; output=root/'build'/f'gate_{bits}.vvp'
    subprocess.run(['iverilog','-g2012','-s','tb_gate',f'-Ptb_gate.BITS={bits}','-o',str(output),'rtl/common/controller.v','rtl/common/linear_engine.v','rtl/common/scan_engine.v','rtl/common/program_sequencer.v','rtl/common/gate_engine.v','rtl/common/sequence_engine.v','rtl/common/sample_engine.v','tests/rtl/tb_gate.v'],cwd=root,check=True,capture_output=True,text=True)
    r=subprocess.run(['vvp',str(output)],cwd=root,capture_output=True,text=True,timeout=20)
    assert r.returncode==0,r.stdout+r.stderr
    assert 'PASS gate' in r.stdout


def test_gate_simulator_edges_and_early_return(monkeypatch):
    now=[0.]; monkeypatch.setattr('host.simulator.time.monotonic',lambda:now[0])
    sim=Simulator(); device=Device(sim)
    p=Profile(mode='ramp',timing_mode='gate',initial=512,safe=123,amplitude=0,rise_ms=10,fall_ms=10,trigger_input=1)
    device.upload(p); device.command(ARM)
    sim.external_trigger(0,'rising'); assert device.status()['state']==1
    sim.external_trigger(1,'rising'); now[0]+=.011
    assert device.status()['code']==0 and sim.phase==2
    now[0]+=1000; assert device.status()['code']==0
    sim.external_trigger(1,'falling'); now[0]+=.011
    assert device.status()['state']==1 and sim.loops==1 and sim.code==512
    sim.external_trigger(1,'rising'); now[0]+=.004
    before=device.status()['code']; sim.external_trigger(1,'falling')
    assert sim.code==before and sim.phase==4
    now[0]+=.005; status=device.status(); assert before<status['code']<512
    now[0]+=.006; assert device.status()['state']==1 and sim.loops==2
    device.command(ABORT); assert device.status()['code']==123


def test_gate_compatibility_and_phase_boundary():
    p=Profile(mode='ramp',timing_mode='gate',initial=512,amplitude=512,rise_ms=.000005,fall_ms=.000005)
    assert compile_samples(p).runs==((512,1),(512,1))
    with pytest.raises(ValueError,match='固件'):compile_profile(p,replace(Capabilities(),gate_mode=False))
    for changes in ({'rise_ms':0},{'fall_ms':0},{'trigger':'software'},{'trigger_edge':'falling'},{'mode':'segments'},{'mode':'function','function_layout':'single'}):
        with pytest.raises(ValueError):compile_profile(replace(p,**changes))
    data=p.to_dict(); data.pop('timing_mode'); assert Profile.from_dict(data).timing_mode=='fixed'


def test_old_firmware_rejected_before_upload():
    class OldFirmware(Simulator):
        def __init__(self):super().__init__(); self.commands=[]
        def exchange(self,frame):
            self.commands.append(frame[3]); reply=super().exchange(frame)
            if frame[3]==INFO:
                payload=bytearray(reply[6:30]); payload[17]=1
                reply=encode(reply[3],int.from_bytes(reply[4:6],'little'),payload)
            return reply
    sim=OldFirmware(); device=Device(sim)
    assert not device.caps.gate_mode
    with pytest.raises(ValueError,match='固件'):device.upload(Profile(mode='ramp',timing_mode='gate'))
    # Connection and immediate preflight both query INFO; no download command.
    assert sim.commands==[INFO,INFO] and sim.pending is None and sim.active is None


def test_gaussian_gate_retains_two_full_curves_across_compile_chunks():
    p=Profile(mode='function',timing_mode='gate',initial=512,amplitude=0,rise_ms=2,hold_ms=1000,fall_ms=2)
    samples=compile_samples(p)
    assert samples.total==800000 and samples.max_error<=.5
    assert sum(n for _,n in samples.runs[:samples.rise_runs])==400000
    assert sum(n for _,n in samples.runs[samples.rise_runs:])==400000
    assert samples.runs[0][0]==512 and samples.runs[samples.rise_runs][0]==0


def test_gate_gui(tmp_path,monkeypatch):
    import os
    os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
    from PySide6.QtWidgets import QApplication,QMessageBox
    from PySide6.QtTest import QTest
    from host.app import Studio
    from host.storage import Library
    from tests.test_gui import until
    errors=[]; monkeypatch.setattr(QMessageBox,'warning',lambda *a:errors.append(a[-1]))
    app=QApplication.instance() or QApplication([]); w=Studio(Library(tmp_path)); w.resize(1500,1300); w.show()
    try:
        w.rise_ms.setValue(.001); w.fall_ms.setValue(.001); w.timing_mode.setCurrentIndex(1)
        assert w.profile.timing_mode=='gate' and not w.repeat.isEnabled() and not w.trigger_edge.isEnabled()
        assert w.hold_ms.isHidden() and '输入决定' in w.duration_label.text()
        w.connect_button.click(); until(app,lambda:w.connected and w.last_status is not None)
        w.upload_button.click(); until(app,lambda:not w.busy and w.uploaded_id is not None)
        w.arm_button.click(); until(app,lambda:not w.busy and w.last_status['state']==1)
        w.external_button.click(); until(app,lambda:not w.busy and w.last_status.get('gate_phase')==2)
        assert w.falling_button.isEnabled()
        w.grab().save(str(Path(__file__).resolve().parents[1]/'build/gate_gui.png'))
        w.falling_button.click(); until(app,lambda:not w.busy and w.last_status['state']==1)
        assert w.last_status['loops']==1 and w.external_button.isEnabled()
        w.disarm_button.click(); until(app,lambda:not w.busy and w.last_status['state']==0)
        assert not errors
    finally:
        w.saved_fingerprint=__import__('host.model',fromlist=['fingerprint']).fingerprint(w.profile)
        if w.connected:w.toggle_connect(); until(app,lambda:not w.connected and not w.busy)
        w.close(); app.processEvents()
