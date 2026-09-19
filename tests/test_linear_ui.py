"""Interpolation settings remain explicit across editing and workspace transfer."""
from dataclasses import replace
import json

import pytest

from host.model import Profile, fingerprint
from host.workspace_archive import _profile
from tests.test_workspace_transfer import desktop
from tests.test_gui import until
from tests.test_interpolation_compiler import gaussian


def test_designer_preserves_width_mode_precision_and_checks_all_points(desktop, tmp_path):
    app, make, close, warnings, commands = desktop
    w=make()
    w.board_page.dac_bits.setCurrentIndex(w.board_page.dac_bits.findData(14))
    w.board_page.dac_count.setValue(4)
    p=gaussian(name='14 位门控高斯 · 插值')
    w.load_profile(p)
    w.rise_ms.setUnit('µs',preserve_duration=True)
    w.fall_ms.setUnit('µs',preserve_duration=True)
    w.resize(1440,1080)
    w.navigate(0)
    app.processEvents()
    assert w.design_bits.currentData()==14 and w.playback.currentData()=='linear'
    assert w.interpolation_error.value()==1 and w.profile_valid
    assert w.collect().initial==5685
    assert w.interpolation_error.isVisible()
    w.compile_button.click()
    until(app,lambda:not w.busy,timeout=30)
    assert '139 / 2,048' in w.validation.text()
    assert '28,000' in w.validation.text() and '最大差异 1' in w.validation.text()
    assert not warnings,warnings
    from pathlib import Path
    image=Path(__file__).resolve().parents[1]/'build'/'linear14_designer.png'
    assert w.grab().save(str(image))
    before=fingerprint(w.collect())
    # Selecting another board must not rewrite an experimental profile.
    w.board_page.dac_bits.setCurrentIndex(w.board_page.dac_bits.findData(10))
    assert fingerprint(w.collect())==before
    w.interpolation_error.setValue(2)
    w.save_profile()
    saved=next(q for q in w.library.list() if q.id==p.id)
    assert (saved.dac_bits,saved.initial,saved.playback,saved.interpolation_error)==(14,5685,'linear',2)
    _profile(saved.to_dict(),'saved')
    close(w)
    reopened=make()
    assert (reopened.collect().dac_bits,reopened.collect().playback,reopened.collect().interpolation_error)==(14,'linear',2)
    assert reopened.collect().initial==5685
    close(reopened)


def test_old_profile_is_not_migrated_when_loaded_on_14bit_board(desktop):
    app, make, close, warnings, commands=desktop
    w=make()
    p=Profile(mode='ramp',dac_bits=10,initial=355,amplitude=0,safe=512)
    old=fingerprint(p)
    w.board_page.dac_bits.setCurrentIndex(w.board_page.dac_bits.findData(14))
    w.load_profile(p)
    assert fingerprint(w.collect())==old
    assert w.design_bits.currentData()==10
    assert w.playback.currentData()=='hold' and not w.interpolation_error.isVisible()
    assert 'playback' not in w.collect().to_dict()
    close(w)


@pytest.mark.parametrize('key,value',[('playback','unknown'),('interpolation_error',1.5),('interpolation_error',-1),('interpolation_error',True)])
def test_archive_rejects_invalid_interpolation_settings(key,value):
    data=gaussian().to_dict();data[key]=value
    with pytest.raises(ValueError):_profile(data,'invalid profile')


def test_workspace_accepts_linear_sequence_snapshot_without_evaluating(monkeypatch):
    from host.functions import values
    p=gaussian()
    data=Profile(mode='sequence',sequence_mode='events',dac_bits=14,initial=5685,safe=5685,
        sequence_steps=[dict(waveform=p.to_dict(),kind='gate',input=0,edge='rising',repeat=1,delay_ms=0)]).to_dict()
    def no_eval(*args,**kwargs):raise AssertionError('import must not evaluate expressions')
    monkeypatch.setattr('host.functions.values',no_eval)
    _profile(data,'linear event sequence')
