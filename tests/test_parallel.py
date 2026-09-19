from pathlib import Path
import os
import subprocess
import shutil
import pytest
from host.model import Profile,fingerprint
from host.protocol import Device,ARM,TRIGGER,ABORT
from host.parallel_simulator import ParallelSimulator


@pytest.mark.parametrize('bits',[10,12,14])
def test_parallel_isolation_group_commands_and_rtl(bits):
    sim=ParallelSimulator(4,2,bits);device=Device(sim);mid=1<<(bits-1)
    assert device.caps.parallel_channels and device.caps.max_runs==2048
    for i in range(4):device.set_static(i,mid+i,mid)
    assert [s['code'] for s in device.status_all()]==list(range(mid,mid+4))
    device.channel_command(ABORT,2)
    assert [s['code'] for s in device.status_all()]==[mid,mid+1,mid,mid+3]
    for i in range(4):
        device.upload(Profile(mode='ramp',channel=i,dac_bits=bits,initial=mid,safe=mid,amplitude=mid-4-i,
                              rise_ms=.01,hold_ms=1,fall_ms=.01,repeat=2000,trigger='software'))
    device.group_command(ARM,[0,1,2,3]);device.group_command(TRIGGER,[0,1,2,3])
    assert len({c.started for c in sim.channels})==1
    assert all(s['state']==2 for s in device.status_all())
    # Whole group rejects ARM without disturbing running channels.
    with pytest.raises(RuntimeError):device.group_command(ARM,[0,1,2,3])
    assert all(s['state']==2 for s in device.status_all())
    device.group_command(ABORT,[0,1,2,3]);assert all(s['code']==mid for s in device.status_all())
    if not shutil.which('iverilog'):pytest.skip('Icarus not installed')
    out=Path('build')/f'parallel_{bits}.vvp'
    subprocess.run(['iverilog','-g2012','-s','tb_parallel',f'-Ptb_parallel.BITS={bits}','-o',str(out),
                    *[str(p) for p in Path('rtl/common').glob('*.v')],'tests/rtl/tb_parallel.v'],check=True,capture_output=True,text=True)
    result=subprocess.run(['vvp',str(out)],check=True,capture_output=True,text=True,timeout=30)
    assert 'PASS parallel' in result.stdout


def test_parallel_page_demo_workflow_and_channel_preservation(tmp_path,monkeypatch):
    os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
    from PySide6.QtWidgets import QApplication,QMessageBox
    from host.storage import Library
    from host.app import Studio
    from tests.test_gui import until
    app=QApplication.instance() or QApplication([]);app.setStyle('Fusion')
    errors=[];monkeypatch.setattr(QMessageBox,'warning',lambda *a:errors.append(a[-1]))
    library=Library(tmp_path)
    profiles=[]
    for i in range(4):
        p=Profile(name=f'并行验证 {i+1}',mode='function' if i%2 else 'ramp',initial=512,safe=512,amplitude=508-i,
                  channel=i,rise_ms=.01,hold_ms=1,fall_ms=.01,repeat=2000,trigger='software')
        library.save(p);profiles.append(p)
    w=Studio(library);w.resize(1440,1000);w.show();app.processEvents()
    try:
        w.board_page.dac_count.setValue(4);w.connect_button.click()
        until(app,lambda:w.connected and len(w.channel_statuses)==4)
        w.navigate(4);page=w.parallel_page
        for row,p in zip(page.rows,profiles):row['combo'].setCurrentIndex(row['combo'].findData(p.id))
        page.download.click();until(app,lambda:not w.busy and len(page.identities)==4,timeout=30)
        until(app,lambda:page.arm.isEnabled());page.arm.click();until(app,lambda:not w.busy and page.trigger.isEnabled())
        page.trigger.click();until(app,lambda:not w.busy and all(s['state']==2 for s in w.channel_statuses.values()))
        page.abort.click();until(app,lambda:not w.busy and all(s['state']==0 for s in w.channel_statuses.values()))
        for i,row in enumerate(page.rows):
            w.navigate(4);row['debug'].click();assert w.debug_page.channel.currentData()==i
            w.debug_page.code.setValue(510+i);w.debug_page.apply_button.click();until(app,lambda:not w.busy)
        until(app,lambda:[w.channel_statuses[i]['code'] for i in range(4)]==[510,511,512,513])
        w.navigate(4)
        w.grab().save('build/ramp_studio_parallel.png')
        assert not errors,errors
        # Main editor can retain downloads independently across channel changes.
        w.navigate(0);w.load_profile(profiles[0]);w.upload_button.click()
        until(app,lambda:not w.busy and w.uploaded_id is not None)
        identity=w.uploaded_id;w.channel.setCurrentIndex(1);app.processEvents();w.channel.setCurrentIndex(0)
        until(app,lambda:w.uploaded_id==identity)
    finally:
        w.saved_fingerprint=fingerprint(w.profile)
        if w.connected:w.send('disconnect',None);until(app,lambda:not w.connected and not w.busy)
        w.close();app.processEvents()
