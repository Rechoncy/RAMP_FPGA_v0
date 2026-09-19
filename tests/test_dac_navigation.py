import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from dataclasses import replace
from PySide6.QtWidgets import QApplication
from PySide6.QtTest import QTest
from host.app import Studio
from host.storage import Library
from host.model import Profile,fingerprint
from tests.test_gui import until


def make_plan(library,name='公共方案',channel=0):
    p=Profile(name=name,mode='ramp',channel=channel,initial=512,safe=512,amplitude=508,
              rise_ms=.001,hold_ms=.001,fall_ms=.001,trigger='software')
    library.save(p);return p


def test_designer_draft_and_channel_assignments_are_separate_and_persist(tmp_path):
    app=QApplication.instance() or QApplication([]);app.setStyle('Fusion')
    library=Library(tmp_path);p=make_plan(library,channel=3);w=Studio(library);w.resize(1440,1000);w.show()
    w.board_page.dac_count.setValue(4);w.board_page.save();w.load_profile(p)
    commands=[];w.request.connect(lambda *args:commands.append(args))
    try:
        assert w.profile.channel==0 # saved templates have no output-channel binding in the designer
        assert w.library_card.isVisible() and w.designer_runtime.isHidden()
        w.name.setText('尚未保存的草稿');w.rise_ms.setUnit('ms',preserve_duration=True);w.rise_ms.setValue(.25);w.rise_ms.setUnit('µs')
        w.mode.setCurrentIndex(w.mode.findData('function'));w.rise_function.setText('exp(')
        assert not w.profile_valid
        for ch in (0,1,3):
            w.dac_buttons[ch].click();page=w.channel_page
            page.plan.setCurrentIndex(page.plan.findData(p.id))
            assert w.stack.currentIndex()==5 and page.channel==ch and page.profile().channel==ch
            assert page.profile().mode=='ramp' and page.profile().initial==512
            assert w.parallel_page.rows[ch]['combo'].currentData()==p.id
        assert not commands
        assert next(x for x in library.list() if x.id==p.id).channel==3
        assert len(library.list())==len(w.parallel_page.profiles)+sum(x.mode not in ('ramp','function') for x in library.list())
        w.nav_buttons[0].click();assert w.rise_function.text()=='exp(' and not w.profile_valid
        assert w.rise_ms.unit.currentData()=='µs' and w.rise_ms.value()==.00025 and w.rise_ms.spin.value()==.25
        w.open_dac(3);QTest.qWait(250);w.grab().save('build/channel_assignment.png')
    finally:w.close();app.processEvents()
    restored=Studio(Library(tmp_path));restored.show()
    try:
        assert restored.stack.currentIndex()==5 and restored.active_dac==3 and restored.rise_function.text()=='exp('
        assert restored.rise_ms.value()==.00025 and restored.rise_ms.spin.value()==.25 and restored.rise_ms.unit.currentData()=='µs'
        for ch in (0,1,3):
            restored.open_dac(ch);assert restored.channel_page.plan.currentData()==p.id
        restored.parallel_page.rows[3]['combo'].setCurrentIndex(0)
        restored.open_dac(3);assert restored.channel_page.plan.currentData() is None
    finally:restored.close();app.processEvents()


def test_saved_versions_download_identity_and_multi_page_control(tmp_path):
    app=QApplication.instance() or QApplication([]);library=Library(tmp_path)
    p=make_plan(library);other=replace(p,id='a'*32,name='另一个方案',initial=513);library.save(other)
    w=Studio(library);w.show();w.board_page.dac_count.setValue(4);w.connect_button.click()
    until(app,lambda:w.connected and len(w.channel_statuses)==4)
    page=w.channel_page
    try:
        for ch in (0,3):
            w.open_dac(ch);page.plan.setCurrentIndex(page.plan.findData(p.id))
            page.buttons['upload'].click();until(app,lambda:not w.busy and page.buttons['arm'].isEnabled())
        ids={ch:w.channel_statuses[ch]['config_id'] for ch in (0,3)}
        w.navigate(0);w.load_profile(p);w.initial.setValue(500)
        w.open_dac(3);assert page.profile().initial==512 and page.buttons['arm'].isEnabled()
        page.buttons['arm'].click();until(app,lambda:not w.busy and w.channel_statuses[3]['state']==1)
        assert w.channel_statuses[0]['state']==0 and w.channel_statuses[0]['config_id']==ids[0]
        page.buttons['disarm'].click();until(app,lambda:not w.busy and w.channel_statuses[3]['state']==0)
        page.plan.setCurrentIndex(page.plan.findData(other.id));assert not page.buttons['arm'].isEnabled()
        page.plan.setCurrentIndex(page.plan.findData(p.id));assert page.buttons['arm'].isEnabled()
        # Saving a revision changes future downloads only; stale firmware cannot be armed as that revision.
        w.navigate(0);assert w.save_profile();w.open_dac(3)
        assert page.profile().initial==500 and not page.buttons['arm'].isEnabled()
        assert w.channel_statuses[3]['config_id']==ids[3] and w.channel_statuses[3]['code']==512
        page.buttons['upload'].click();until(app,lambda:not w.busy and page.buttons['arm'].isEnabled())
        assert w.channel_statuses[0]['config_id']==ids[0]
        # The simultaneous controller shares assignments and download confirmations.
        w.navigate(4);pool=w.parallel_page
        for i,row in enumerate(pool.rows):row['check'].setChecked(i==3)
        assert pool.arm.isEnabled()
        previous_code=w.channel_statuses[3]['code'];pool.rows[3]['debug'].click()
        assert w.stack.currentIndex()==3 and w.debug_page.channel.currentData()==3
        assert w.channel_statuses[3]['code']==previous_code
        w.debug_page.code.setValue(511);w.debug_page.apply_button.click();until(app,lambda:not w.busy)
        w.open_dac(3);assert not page.buttons['arm'].isEnabled() and page.buttons['upload'].isEnabled()
        # Archived plans disappear from both selectors and cannot execute.
        library.archive(next(x for x in library.list() if x.id==p.id));w.refresh_library()
        assert page.plan.currentData() is None and not page.buttons['upload'].isEnabled()
    finally:
        if w.connected:w.send('disconnect',None);until(app,lambda:not w.connected and not w.busy)
        w.close();app.processEvents()



import pytest
@pytest.mark.parametrize('count',[2,4])
def test_channel_page_gate_and_reconnect_require_confirmation(tmp_path,count):
    app=QApplication.instance() or QApplication([]);library=Library(tmp_path)
    p=make_plan(library);p.timing_mode='gate';p.trigger='external';library.save(p)
    w=Studio(library);w.board_page.dac_count.setValue(count);w.show();w.connect_button.click()
    until(app,lambda:w.connected and w.last_status is not None)
    page=w.channel_page
    try:
        w.open_dac(count-1);page.plan.setCurrentIndex(page.plan.findData(p.id))
        page.buttons['upload'].click();until(app,lambda:not w.busy and page.buttons['arm'].isEnabled())
        assert '设备已确认' in page.feedback.text()
        page.buttons['arm'].click();until(app,lambda:not w.busy and page.buttons['external'].isEnabled())
        page.buttons['external'].click();until(app,lambda:not w.busy and page.buttons['falling'].isEnabled())
        assert '软件触发'==page.buttons['trigger'].text() and not page.buttons['trigger'].isEnabled()
        page.buttons['falling'].click();until(app,lambda:not w.busy and page.buttons['external'].isEnabled())
        page.buttons['abort'].click();until(app,lambda:not w.busy and page.buttons['arm'].isEnabled())
        w.send('disconnect',None);until(app,lambda:not w.connected and not w.busy)
        assert not page.buttons['arm'].isEnabled() and page.plan.currentData()==p.id
        w.connect_button.click();until(app,lambda:w.connected and w.last_status is not None)
        assert not page.buttons['arm'].isEnabled() and page.buttons['upload'].isEnabled()
    finally:
        if w.connected:w.send('disconnect',None);until(app,lambda:not w.connected and not w.busy)
        w.close();app.processEvents()


def test_legacy_drafts_recover_as_copies_without_overwriting_source(tmp_path,monkeypatch):
    import json
    from PySide6.QtWidgets import QInputDialog
    app=QApplication.instance() or QApplication([]);library=Library(tmp_path);p=make_plan(library)
    draft=lambda name,expr:{'profile':p.to_dict(),'texts':{'name':name,'rise_function':expr},'combos':{'mode':'function'}}
    source=tmp_path/'dac_editors.json'
    source.write_text(json.dumps({'selected':3,'drafts':{'0':draft('旧草稿 A','exp('),'3':draft('旧草稿 B','I')}}),encoding='utf-8')
    original=source.read_bytes();w=Studio(library)
    try:
        assert w.name.text()=='旧草稿 B' and w.channel.currentData()==0
        monkeypatch.setattr(w,'maybe_discard',lambda:True)
        monkeypatch.setattr(QInputDialog,'getItem',lambda *a:(a[3][0],True))
        w.recover_legacy_draft()
        assert w.name.text()=='旧草稿 A · 恢复副本' and w.rise_function.text()=='exp('
        assert not w.profile_valid and w.profile.id!=p.id
        assert source.read_bytes()==original
    finally:w.close();app.processEvents()
    assert source.read_bytes()==original and (tmp_path/'designer_draft.json').exists()
