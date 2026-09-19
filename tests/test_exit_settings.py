"""Exit/restart preserves editing state without changing saved plans or outputs."""
import json
import os
from dataclasses import replace
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
import pytest
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication,QMessageBox
from host.app import Studio
from host.model import Profile,fingerprint
from host.storage import Library
from tests.test_gui import until


@pytest.fixture
def desktop(tmp_path,monkeypatch):
    QSettings.setDefaultFormat(QSettings.IniFormat)
    QSettings.setPath(QSettings.IniFormat,QSettings.UserScope,str(tmp_path/'preferences'))
    app=QApplication.instance() or QApplication([]);app.setStyle('Fusion')
    windows=[];warnings=[]
    monkeypatch.setattr(QMessageBox,'warning',lambda *args:warnings.append(args[-1]))
    def make():
        w=Studio(Library(tmp_path/'data'));windows.append(w);w.show();app.processEvents();return w
    def close(w):
        w.close();until(app,lambda:not w.thread.isRunning(),timeout=20)
    yield app,make,close,warnings
    for w in windows:
        if w.thread.isRunning():
            if w.connected:w.send('disconnect',None);until(app,lambda:not w.busy and not w.connected)
            w.close();until(app,lambda:not w.thread.isRunning(),timeout=20)


def small_wave():
    return Profile(mode='ramp',name='已保存的实验',initial=512,safe=512,amplitude=508,
                   rise_ms=.001,hold_ms=.001,fall_ms=.001)


def test_pending_input_invalid_draft_and_saved_library_separation(desktop):
    app,make,close,warnings=desktop;w=make();wave=small_wave();w.library.save(wave);w.refresh_library();w.load_profile(wave)
    originals={p.name:p.read_bytes() for p in w.library.root.glob('profiles/*.json')}
    w.name.setText('退出保留 · 尚未保存');w.mode.setCurrentIndex(w.mode.findData('function'))
    w.rise_function.setText('exp(');w.repeat.setValue(29);w.repeat_mode.setCurrentIndex(1)
    w.rise_ms.setUnit('µs',preserve_duration=True)
    w.rise_ms.spin.lineEdit().setText('17.25') # no focus-out/valueChanged yet
    assert w.rise_ms.value()!=.01725
    close(w);other=make()
    assert other.name.text()=='退出保留 · 尚未保存' and other.rise_function.text()=='exp('
    assert not other.profile_valid and other.repeat_mode.currentData()=='infinite' and other.repeat.value()==29
    assert other.rise_ms.unit.currentData()=='µs' and other.rise_ms.value()==pytest.approx(.01725)
    assert other.rise_ms.spin.value()==17.25
    assert {p.name:p.read_bytes() for p in other.library.root.glob('profiles/*.json')}==originals
    assert not warnings


def test_board_flash_assignments_and_view_restart_offline(desktop):
    app,make,close,warnings=desktop;w=make();wave=small_wave();w.library.save(wave);w.refresh_library()
    board=w.board_page;board.name.setText('自动保存的四路板卡');board.dac_count.setValue(4);board.ref.setValue(100)
    board.flash_page.bit.setText('D:/未选择完成/boot.bit');board.flash_page.expected.setText('ABCDEF01')
    board.tabs.setCurrentIndex(1)
    w.ports.addItem('测试未连接串口','COM987');w.ports.setCurrentIndex(w.ports.findData('COM987'))
    w.open_dac(3);cp=w.channel_page;cp.plan.setCurrentIndex(cp.plan.findData(wave.id))
    cp.view.setCurrentIndex(cp.view.findData('local'));cp.settings_section.toggle.setChecked(True)
    w.parallel_page.store.override(3,dict(trigger='external',trigger_input=1,trigger_edge='falling'))
    w.debug_page.channel.setCurrentIndex(w.debug_page.channel.findData(2));w.debug_page.code.setValue(37);w.debug_page.safe.setValue(511)
    commands=[];w.request.connect(lambda *args:commands.append(args[0]));close(w)
    assert not commands
    assert json.loads((w.library.root/'board.json').read_text('utf-8'))['ref_mhz']==100
    other=make()
    assert other.board_page.name.text()=='自动保存的四路板卡' and other.caps.dac_count==4
    assert other.board_page.ref.value()==100 and other.board_page.tabs.currentIndex()==1
    assert other.board_page.flash_page.bit.text()=='D:/未选择完成/boot.bit'
    assert other.board_page.flash_page.expected.text()=='ABCDEF01'
    assert other.ports.currentData()=='COM987';other.refresh_ports();assert other.ports.currentData()=='COM987'
    assert other.stack.currentIndex()==5 and other.active_dac==3 and other.channel_page.channel==3
    assert other.channel_page.plan.currentData()==wave.id and other.channel_page.view.currentData()=='local'
    assert other.channel_page.settings_section.toggle.isChecked()
    assert other.channel_page.profile().trigger_input==1 and other.channel_page.profile().trigger_edge=='falling'
    assert other.debug_page.channel.currentData()==2 and other.debug_page.code.value()==37 and other.debug_page.safe.value()==511
    assert not other.connected and not other.busy and not other.channel_statuses and not other.debug_page.confirmed
    assert not other.parallel_page.store.identities and not other.channel_page.buttons['arm'].isEnabled()
    assert not warnings


def test_invalid_board_sequence_and_scan_drafts(desktop):
    app,make,close,warnings=desktop;w=make();assert w.board_page.save()
    known_board=(w.library.root/'board.json').read_bytes();w.board_page.delay_min.setText('还未确认')
    w.board_page.ref.lineEdit().setText(' MHz')
    wave=small_wave();steps=[dict(waveform=wave.to_dict(),kind='edge',start_ms=v,delay_ms=0,input=0,edge='rising',repeat=1) for v in (0,.01)]
    seq=w.sequence_page;seq.load(Profile(mode='sequence',name='未保存序列',sequence_steps=steps,repeat=0),saved=False)
    seq.repeat.setValue(17);seq.time.setUnit('ns',preserve_duration=True);seq.table.selectRow(1)
    scan=w.scan_page;scan.load(Profile(mode='scan',name='未完成扫描',scan_start=1000,scan_step=100,scan_points=3),saved=False)
    assert not scan.valid
    w.navigate(0);w.designer_tabs.setCurrentIndex(1);seq.time.spin.setFocus();seq.time.spin.lineEdit().setText('23456')
    close(w);other=make()
    assert (other.library.root/'board.json').read_bytes()==known_board
    assert other.board_page.delay_min.text()=='还未确认'
    assert other.board_page.ref.lineEdit().text()==' MHz'
    seq=other.sequence_page
    assert seq.collect().repeat==0 and seq.repeat.value()==17 and seq.current_row==1
    assert seq.time.unit.currentData()=='ns' and seq.time.spin.value()==23456
    assert seq.steps[1]['start_ms']==pytest.approx(.023456)
    assert other.scan_page.collect().scan_step==100 and not other.scan_page.valid
    assert other.designer_tabs.currentIndex()==1 and other.stack.currentIndex()==0
    assert not warnings


def test_failed_save_keeps_window_and_previous_snapshot(desktop,monkeypatch):
    import host.session as session
    app,make,close,warnings=desktop;w=make();w.session.save(w)
    before=w.session.path.read_bytes();original=session.atomic_json;w.name.setText('写入失败时不能丢失')
    def fail(path,data):
        if path==w.session.path:raise OSError('模拟磁盘写入失败')
        return original(path,data)
    monkeypatch.setattr(session,'atomic_json',fail)
    assert not w.close();app.processEvents()
    assert w.isVisible() and w.thread.isRunning() and w.centralWidget().isEnabled() and not w._exit_saved
    assert w.session.path.read_bytes()==before and w.name.text()=='写入失败时不能丢失'
    assert len(warnings)==1 and '模拟磁盘写入失败' in warnings[0]
    monkeypatch.setattr(session,'atomic_json',original);close(w);other=make()
    assert other.name.text()=='写入失败时不能丢失'


def test_connected_exit_only_disconnects_and_malformed_snapshot_is_backed_up(desktop):
    app,make,close,warnings=desktop;w=make();w.connect_button.click()
    until(app,lambda:w.connected and not w.busy)
    commands=[];w.request.connect(lambda *args:commands.append(args[0]));w.name.setText('已连接退出的草稿');close(w)
    assert commands==['disconnect']
    other=make();assert not other.connected and other.name.text()=='已连接退出的草稿';close(other)
    raw=json.dumps(dict(schema=1,board={'schema':99},view=['bad'],assignments={'0':None}),ensure_ascii=False).encode('utf-8')
    other.session.path.write_bytes(raw)
    third=make();assert third.session.error and not third.connected;close(third)
    assert any(p.read_bytes()==raw for p in (third.library.root/'archive/settings_recovery').glob('*.json'))
