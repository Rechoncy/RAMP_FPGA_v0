"""Standalone infinite waveforms retain their design, preview and stop scope."""
from dataclasses import replace
from pathlib import Path
import json
import pytest
from PySide6.QtWidgets import QApplication
from PySide6.QtTest import QTest
from host.app import Studio
from host.model import Profile
from host.storage import Library
from tests.test_gui import until

ROOT=Path(__file__).resolve().parents[1]

def make_window(tmp_path):
    app=QApplication.instance() or QApplication([]);app.setStyle('Fusion')
    w=Studio(Library(tmp_path));w.show()
    return app,w

def close(app,w):
    if w.connected:w.send('disconnect',None);until(app,lambda:not w.connected and not w.busy)
    w.close();until(app,lambda:not w.thread.isRunning())

def wave(mode='function',layout='single'):
    return Profile(mode=mode,function_layout=layout,name='单波形无限循环',initial=512,safe=510,amplitude=508,
                   rise_ms=.001,hold_ms=.001,fall_ms=.001,function='I + 20 + 10*sin(2*pi*t/T)',function_ms=.01)

@pytest.mark.parametrize('mode,layout',[('ramp','staged'),('function','staged'),('function','single')])
def test_infinite_choice_finite_restore_save_reload_and_gate(tmp_path,mode,layout):
    app,w=make_window(tmp_path)
    try:
        w.load_profile(wave(mode,layout));w.repeat.setValue(29)
        w.repeat_mode.setCurrentIndex(w.repeat_mode.findData('infinite'))
        assert w.collect().repeat==0 and w.profile_valid and w.repeat.isHidden()
        assert '无限循环' in w.duration_label.text() and '总计 0' not in w.duration_label.text()
        assert w.plot.profile.repeat==0 and w.plot.caps.infinite_repeat
        w.repeat_mode.setCurrentIndex(0);assert w.collect().repeat==29 and not w.repeat.isHidden()
        w.repeat_mode.setCurrentIndex(1)
        if mode=='ramp' or layout=='staged':
            w.timing_mode.setCurrentIndex(w.timing_mode.findData('gate'))
            assert w.collect().repeat==29 and not w.repeat_box.isVisible() and not w.repeat_mode.isEnabled()
            w.timing_mode.setCurrentIndex(0);assert w.collect().repeat==0 and w.repeat_mode.isEnabled()
        assert w.save_profile();p=next(p for p in w.library.list() if p.id==w.profile.id)
        assert p.repeat==0
        w.load_profile(p);assert w.collect().repeat==0 and w.repeat_mode.currentData()=='infinite'
        w.repeat_mode.setCurrentIndex(0);w.repeat.setValue(29);w.repeat_mode.setCurrentIndex(1)
        w.open_dac(0);w.channel_page.plan.setCurrentIndex(w.channel_page.plan.findData(p.id))
        assert '新版 FPGA' not in w.channel_page.version.text()
        w.channel_page.view.setCurrentIndex(w.channel_page.view.findData('local'))
        assert len(w.channel_page.plot.curves)==1 and '无限循环' in w.channel_page.details.text()
        assert w.channel_page.plot.curves[0][0][1][-1]==w.channel_page.plot.curves[0][0][1][0]
        w.navigate(0)
    finally:close(app,w)
    app,w=make_window(tmp_path)
    try:
        assert w.repeat_mode.currentData()=='infinite' and w.collect().repeat==0
        w.repeat_mode.setCurrentIndex(0);assert w.collect().repeat==29
        if mode=='function' and layout=='single':
            w.repeat_mode.setCurrentIndex(1)
            out=ROOT/'build/wave_infinite_release';out.mkdir(exist_ok=True)
            for width,height in ((1440,1000),(1080,700)):
                w.resize(width,height);QTest.qWait(150)
                assert w.repeat_mode.minimumSizeHint().width()<=w.repeat_mode.width()
                assert w.grab().save(str(out/f'editor-{width}.png'))
    finally:close(app,w)

def test_infinite_prepare_trigger_abort_and_old_capability(tmp_path):
    app,w=make_window(tmp_path)
    try:
        w.load_profile(wave());w.repeat_mode.setCurrentIndex(1);assert w.save_profile();pid=w.profile.id
        w.board_page.dac_count.setValue(4)
        w.connect_button.click();until(app,lambda:w.connected and not w.busy and len(w.channel_statuses)==4)
        w.open_dac(0);cp=w.channel_page;cp.plan.setCurrentIndex(cp.plan.findData(pid))
        cp.buttons['prepare'].click();until(app,lambda:not w.busy and w.channel_statuses[0]['state']==1)
        cp.buttons['external'].click();until(app,lambda:not w.busy and w.channel_statuses[0]['state']==2)
        assert w.channel_statuses[0]['infinite_repeat'] and cp.plot.profile.repeat==0
        cp.buttons['abort'].click();until(app,lambda:not w.busy and w.channel_statuses[0]['state']==0)
        assert w.channel_statuses[0]['code']==510
        # Design/save remain available when an older board cannot play it.
        w.caps=replace(w.caps,infinite_repeat=False);w.navigate(0);w.edited()
        assert w.profile_valid and w.save_button.isEnabled() and '不支持' in w.duration_label.text()
        w.open_dac(0);assert not cp.buttons['prepare'].isEnabled()
    finally:close(app,w)
