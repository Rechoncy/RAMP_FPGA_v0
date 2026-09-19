import json
import shutil
import subprocess
from pathlib import Path
import pytest
from host.protocol import Device,ARM,TRIGGER,ABORT,DISARM,STATUS,BEGIN_RUNS,RUN,COMMIT
from host.simulator import Simulator
from host.model import Profile,fingerprint


class RecordingSimulator(Simulator):
    def __init__(self,bits=10):
        super().__init__(8,8,bits); self.frames=[]; self.outputs=[]

    def exchange(self,frame):
        reply=super().exchange(frame)
        self.frames.append(frame)
        codes=[self.mid]*self.dac_count
        if self.active:codes[self.active.get('channel',0)]=self.code
        self.outputs.append(sum(code<<(i*self.dac_bits) for i,code in enumerate(codes)))
        return reply


@pytest.mark.parametrize('bits',[10,12,14])
def test_static_hold_switch_abort_and_rtl(bits):
    sim=RecordingSimulator(bits); device=Device(sim)
    for channel,code in ((7,0),(0,(1<<bits)-1),(3,123)):
        result=device.set_static(channel,code,1<<(bits-1))
        assert result['status']['code']==code and result['status']['channel']==channel
        assert result['status']['state']==0 and sim.active['external']==0
        for trigger in range(8):sim.external_trigger(trigger,'rising')
        sim.refresh(); assert sim.code==code and sim.state==0
    device.command(ABORT); assert device.status()['code']==1<<(bits-1)
    assert not any(frame[3] in (ARM,TRIGGER) for frame in sim.frames)
    assert sum(frame[3]==COMMIT for frame in sim.frames)==3
    if not shutil.which('iverilog'):pytest.skip('Icarus not installed')
    root=Path(__file__).resolve().parents[1]
    (root/'build/debug_vectors.hex').write_text('\n'.join(f'{int.from_bytes(f,"little"):064x}' for f in sim.frames))
    (root/'build/debug_expected.hex').write_text('\n'.join(f'{v:028x}' for v in sim.outputs))
    output=root/'build'/f'debug_{bits}.vvp'
    subprocess.run(['iverilog','-g2012','-s','tb_debug',f'-Ptb_debug.BITS={bits}',f'-Ptb_debug.COUNT={len(sim.frames)}','-o',str(output),
        'rtl/common/controller.v','rtl/common/linear_engine.v','rtl/common/scan_engine.v','rtl/common/program_sequencer.v','rtl/common/gate_engine.v','rtl/common/sequence_engine.v','rtl/common/sample_engine.v','tests/rtl/tb_debug.v'],cwd=root,check=True,capture_output=True,text=True)
    r=subprocess.run(['vvp',str(output)],cwd=root,check=True,capture_output=True,text=True,timeout=20)
    assert 'PASS static' in r.stdout


def test_static_validation_and_busy_state_do_not_overwrite_waveform():
    sim=RecordingSimulator(); device=Device(sim)
    for channel,code,safe in ((8,100,512),(-1,0,0),(0,1024,512),(0,0,-1),(True,0,0)):
        before=len(sim.frames)
        with pytest.raises(ValueError):device.set_static(channel,code,safe)
        assert len(sim.frames)==before
    device.upload(Profile(mode='ramp',initial=512,safe=512,rise_ms=.00001,hold_ms=100,fall_ms=.00001))
    identity=sim.active['id']; device.command(ARM)
    for state in (1,2):
        if state==2:sim.external_trigger()
        with pytest.raises(RuntimeError,match='等待触发'):device.set_static(1,100,512)
        assert sim.active['id']==identity and sim.state==state
    device.command(ABORT); device.set_static(1,100,512)
    assert sim.code==100


def test_static_readback_mismatch_is_not_reported_as_success():
    class ChangedStatus(Simulator):
        def exchange(self,frame):
            if frame[3]==STATUS and self.active:self.code=555
            return super().exchange(frame)
    device=Device(ChangedStatus())
    with pytest.raises(RuntimeError,match='状态回读不一致'):device.set_static(1,100,512)


def test_debug_page_submit_invalidation_bounds_and_return(tmp_path,monkeypatch):
    import os
    os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
    from PySide6.QtWidgets import QApplication,QMessageBox
    from PySide6.QtTest import QTest
    from host.app import Studio
    from host.storage import Library
    from tests.test_gui import until
    errors=[]; monkeypatch.setattr(QMessageBox,'warning',lambda *a:errors.append(a[-1]))
    app=QApplication.instance() or QApplication([]); w=Studio(Library(tmp_path)); w.resize(1440,1100); w.show(); app.processEvents()
    try:
        debug=w.debug_page; w.navigate(3); QTest.qWait(250)
        assert not debug.apply_button.isEnabled()
        w.rise_ms.setValue(.001); w.hold_ms.setValue(.001); w.fall_ms.setValue(.001)
        w.connect_button.click(); until(app,lambda:w.connected and w.last_status is not None)
        w.upload_button.click(); until(app,lambda:not w.busy and w.uploaded_id is not None,timeout=30)
        original=fingerprint(w.profile)
        assert w.arm_button.isEnabled()
        debug.channel.setCurrentIndex(1); debug.code.setValue(123); debug.safe.setValue(400)
        assert w.last_status['code']==512
        debug.apply_button.click(); until(app,lambda:not w.busy)
        assert debug.confirmed and w.last_status['code']==123 and w.last_status['channel']==1
        assert not w.arm_button.isEnabled() and w.uploaded_id is None and fingerprint(w.profile)==original
        debug.safe.setValue(777); debug.abort_button.click(); until(app,lambda:not w.busy)
        assert w.last_status['code']==400  # Uses committed safe code, not the edited field.
        assert not debug.initial_button.isEnabled()
        w.parallel_page.rows[1]['combo'].setCurrentIndex(w.parallel_page.rows[1]['combo'].findData(w.profile.id));w.update_buttons()
        debug.initial_button.click(); until(app,lambda:not w.busy)
        assert w.last_status['code']==512 and debug.confirmed['safe']==777
        debug.code.setValue(1023); debug.apply_button.click(); until(app,lambda:not w.busy)
        assert w.last_status['code']==1023
        w.upload_button.click(); until(app,lambda:not w.busy and w.uploaded_id is not None,timeout=30)
        w.arm_button.click(); until(app,lambda:not w.busy and w.last_status['state']==1)
        assert not debug.apply_button.isEnabled() and debug.disarm_button.isEnabled()
        debug.disarm_button.click(); until(app,lambda:not w.busy and w.last_status['state']==0)
        debug.midpoint_button.click(); until(app,lambda:not w.busy)
        QTest.qWait(250)
        w.grab().save(str(Path(__file__).resolve().parents[1]/'build/debug_static.png'))
        assert not errors,errors
        # Changing the board UI while connected must not replace device capabilities.
        w.board_page.dac_bits.setCurrentIndex(w.board_page.dac_bits.findData(14))
        assert debug.caps.dac_bits==10
        w.connect_button.click(); until(app,lambda:not w.connected and not w.busy)
        debug.code.setValue(16383)
        w.board_page.dac_bits.setCurrentIndex(w.board_page.dac_bits.findData(10))
        assert debug.code.value()==16383 and not debug.apply_button.isEnabled()
    finally:
        w.saved_fingerprint=fingerprint(w.profile)
        if w.connected:
            w.send('disconnect',None); until(app,lambda:not w.connected and not w.busy)
        w.close(); app.processEvents()
