"""Version integrity, preparation failure boundaries and explicit output scope."""
import copy
from dataclasses import replace
import pytest
from PySide6.QtWidgets import QApplication,QMessageBox
from host.model import Profile,fingerprint
from host.storage import Library
from host.output_state import OutputState
from host.output_prepare import prepare_outputs
from host.protocol import Device,ARM,ABORT,DISARM
from host.parallel_simulator import ParallelSimulator
from host.boot import snapshot_assignments


def plan(ch=0,**kwargs):
    return Profile(mode='function',name=f'高斯 {ch+1}',channel=ch,initial=512,safe=512,amplitude=500,
                   rise_ms=.002,hold_ms=.004,fall_ms=.002,**kwargs)


def test_confirmed_snapshot_is_immutable_and_reconnect_requires_exact_id(tmp_path):
    lib=Library(tmp_path);p=plan();lib.save(p);store=OutputState(lib);store.assign(0,p.id);store.save();store.connect('COM-test')
    dev=Device(ParallelSimulator());data=[]
    prepare_outputs(dev,dict(profiles=[p]),applied=data.append)
    store.confirm(p,data[0]['config_id'],dev.caps);status=dev.status(0)
    p.amplitude=490;lib.save(p);store.refresh_library()
    assert store.loaded(0,status,dev.caps).amplitude==500 and store.resolve(0).amplitude==490
    returned=store.loaded(0,status,dev.caps);returned.initial=1
    assert store.loaded(0,status,dev.caps).initial==512
    restored=OutputState(lib);restored.connect('COM-test')
    assert restored.loaded(0,status,dev.caps).amplitude==500
    assert restored.loaded(0,{**status,'config_id':status['config_id']+1},dev.caps) is None
    assert restored.loaded(0,{**status,'valid':False},dev.caps) is None
    assert restored.loaded(1,status,dev.caps) is None
    restored.connect('demo');assert restored.loaded(0,status,dev.caps) is None
    dev.close()


def test_input_overrides_and_flash_selection_are_independent(tmp_path):
    lib=Library(tmp_path);p=plan();lib.save(p);store=OutputState(lib)
    store.assign(0,p.id,False);store.assign(1,p.id,True)
    store.override(0,dict(trigger='external',trigger_input=1,trigger_edge='falling'))
    assert store.resolve(0).trigger_input==1 and store.resolve(1).trigger_input==0
    assert next(x for x in lib.list() if x.id==p.id).trigger_input==0
    snapshot=snapshot_assignments(lib,4,[0])
    assert list(snapshot)==['0'] and snapshot['0']['trigger_edge']=='falling'
    store.assign(0,p.id,True);store.assign(1,p.id,False);store.save()
    assert snapshot_assignments(lib,4,[0])==snapshot
    from tests.test_sequences import example
    events=example(events=True);lib.save(events);store.refresh_library();store.assign(0,events.id)
    original=copy.deepcopy(events.sequence_steps)
    store.override(0,dict(trigger='external',trigger_input=1,trigger_edge='falling'))
    assert store.resolve(0).sequence_steps==original


def test_prepare_reuses_confirmed_config_and_batches_arm(monkeypatch):
    device=Device(ParallelSimulator());p=plan();q=plan(3);applied=[];uploads=[];groups=[]
    original=device.upload;group=device.group_command
    def upload(p,progress):uploads.append(p.channel);return original(p,progress)
    def grouped(command,channels):groups.append((command,channels));return group(command,channels)
    monkeypatch.setattr(device,'upload',upload);monkeypatch.setattr(device,'group_command',grouped)
    result=prepare_outputs(device,dict(profiles=[p,q]),applied=applied.append)
    assert result['uploaded']==2 and uploads==[0,3] and groups==[(ARM,[0,3])]
    assert device.status(1)['valid'] is False
    confirmed={r['profile'].channel:(r['config_id'],fingerprint(r['profile'])) for r in applied}
    # Already armed and unchanged: neither another transfer nor a duplicate ARM.
    result=prepare_outputs(device,dict(profiles=[p,q],confirmed=confirmed))
    assert result['reused']==2 and len(uploads)==2 and len(groups)==1
    device.group_command(ABORT,[0,3]);q=replace(q,amplitude=495)
    result=prepare_outputs(device,dict(profiles=[p,q],confirmed=confirmed))
    assert result['uploaded']==1 and uploads==[0,3,3] and groups[-1]==(ARM,[0,3])
    device.close()


def test_prepare_validates_all_before_write_and_keeps_partial_confirmation(monkeypatch):
    device=Device(ParallelSimulator());p=plan();q=plan(1);applied=[];calls=[]
    original=device.upload
    def fail_second(p,progress):
        calls.append(p.channel)
        if p.channel==1:raise TimeoutError('模拟第二路传输失败')
        return original(p,progress)
    monkeypatch.setattr(device,'upload',fail_second)
    with pytest.raises(ValueError):prepare_outputs(device,dict(profiles=[p,replace(q,amplitude=5000)]))
    assert not calls and not device.status(0)['valid']
    with pytest.raises(RuntimeError,match='已确认下载 1 路'):
        prepare_outputs(device,dict(profiles=[p,q]),applied=applied.append)
    assert calls==[0,1] and len(applied)==1 and applied[0]['profile'].channel==0
    assert device.status(0)['valid'] and device.status(0)['state']==0
    assert not device.status(1)['valid']
    device.close()


def test_detail_scope_loaded_curve_and_flash_do_not_follow_batch_checks(tmp_path,monkeypatch):
    from host.app import Studio
    from tests.test_gui import until
    app=QApplication.instance() or QApplication([]);errors=[]
    monkeypatch.setattr(QMessageBox,'warning',lambda *a:errors.append(a[-1]))
    lib=Library(tmp_path);p=plan(trigger='software');lib.save(p);w=Studio(lib);w.show()
    try:
        w.board_page.dac_count.setValue(4);w.connect_button.click()
        until(app,lambda:w.connected and len(w.channel_statuses)==4)
        pool=w.parallel_page
        for i,r in enumerate(pool.rows):r['check'].setChecked(i in (0,1))
        w.open_dac(3);page=w.channel_page;page.plan.setCurrentIndex(page.plan.findData(p.id))
        assert page.plot.profile is None and page.view.currentData()=='loaded'
        assert page.settings_section.content.isHidden() and page.version_section.content.isHidden()
        page.buttons['prepare'].click();until(app,lambda:not w.busy and w.channel_statuses[3]['state']==1)
        assert pool.selected()==[0,1] and page.plot.profile.channel==3
        assert all(not w.channel_statuses[i]['valid'] for i in (0,1,2))
        assert page.buttons['trigger'].isEnabled() and not page.buttons['upload'].isEnabled()
        loaded=fingerprint(page.plot.profile);old_id=w.channel_statuses[3]['config_id']
        p.amplitude=490;lib.save(p);w.refresh_library()
        assert fingerprint(page.plot.profile)==loaded and page.plot.local.amplitude==490
        assert not page.buttons['trigger'].isEnabled()
        page.view.setCurrentIndex(page.view.findData('compare'));assert len(page.plot.curves)==2
        page.buttons['abort'].click();until(app,lambda:not w.busy and w.channel_statuses[3]['state']==0)
        page.buttons['prepare'].click();until(app,lambda:not w.busy and w.channel_statuses[3]['state']==1)
        assert w.channel_statuses[3]['config_id']!=old_id and page.plot.profile.amplitude==490
        w.navigate(4);assert pool.selected()==[0,1] and pool.rows[3]['plot'].profile.amplitude==490
        flash=w.board_page.flash_page
        for i,c in enumerate(flash.boot_checks):c.setChecked(i==3)
        flash.capture_boot();assert list(flash.boot_profiles)==['3'] and not errors
        assert pool.selected()==[0,1]
    finally:
        if w.connected:w.send('disconnect',None);until(app,lambda:not w.connected and not w.busy)
        w.close();app.processEvents()
