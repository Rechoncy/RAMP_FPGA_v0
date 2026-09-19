import math
import numpy as np
import pytest
from host.model import Profile, Capabilities, compile_profile, fingerprint
from host.functions import compile_samples, stage_counts, values, shape_key, preview_samples, STAGED_TEMPLATES


def expand(result):
    return np.repeat([r[0] for r in result.runs],[r[1] for r in result.runs])


@pytest.mark.parametrize('bits',[10,12,14])
@pytest.mark.parametrize('direction',[-1,1])
def test_half_gaussian_hold_return_at_200mhz(bits,direction):
    initial=1<<(bits-1); target=initial+direction*400
    p=Profile(mode='function',dac_bits=bits,initial=initial,safe=initial,amplitude=target,
              rise_ms=.000100,hold_ms=.000035,fall_ms=.000160)
    assert stage_counts(p)==(20,7,32)
    result=compile_samples(p); actual=expand(result)
    low=math.exp(-4.5)
    expected=[initial+(target-initial)*(math.exp(-4.5*(1-i/20)**2)-low)/(1-low) for i in range(20)]
    expected += [target]*7
    expected += [initial+(target-initial)*(math.exp(-4.5*(i/32)**2)-low)/(1-low) for i in range(32)]
    assert result.total==59 and list(actual)==list(np.floor(np.array(expected)+.5))
    assert actual[0]==initial and np.all(actual[20:28]==target)
    assert np.all(np.diff(actual[:21].astype(int))*direction>=0)
    assert np.all(np.diff(actual[27:].astype(int))*direction<=0)
    assert values(shape_key(p),np.array([59.]),59)[1][0]==initial
    assert result.max_error<=.5
    c=compile_profile(p,Capabilities(dac_bits=bits))
    assert c.duration_ms==pytest.approx(59/200000)
    q=Profile.from_dict(p.to_dict())
    assert fingerprint(q)==fingerprint(p) and compile_samples(q)==result


def test_stage_time_rounding_zero_stages_and_endpoints():
    p=Profile(mode='function',initial=512,amplitude=112,rise_ms=.000007,hold_ms=.000008,fall_ms=.000007)
    # Independently rounded: 1 + 2 + 1 samples, not whole-wave rounding.
    assert stage_counts(p)==(1,2,1) and compile_samples(p).total==4
    assert list(expand(compile_samples(p)))==[512,112,112,112]
    p.rise_ms=0; p.fall_ms=0
    assert compile_samples(p).runs==((112,2),)
    p.rise_ms=.000001
    with pytest.raises(ValueError,match='过短'):compile_samples(p)
    p.rise_ms=.001; p.rise_function='A'
    with pytest.raises(ValueError,match='进入段端点'):compile_samples(p)
    p.rise_function=STAGED_TEMPLATES['线性'][0]; p.fall_ms=.001; p.fall_function='I'
    with pytest.raises(ValueError,match='返回段端点'):compile_samples(p)


def test_custom_stage_local_time_cache_and_overflow():
    up,down=STAGED_TEMPLATES['线性']
    p=Profile(mode='function',initial=512,amplitude=112,rise_ms=.000020,hold_ms=.000010,fall_ms=.000040,
              rise_function=up,fall_function=down)
    assert list(expand(compile_samples(p)))==[512,412,312,212,112,112,112,162,212,262,312,362,412,462]
    before=compile_samples(p)
    p.fall_function='A + (I-A)*(t/T)**2'
    assert compile_samples(p)!=before
    p.rise_function='I + (A-I)*t/T + 2000*sin(pi*t/T)'
    with pytest.raises(ValueError,match='原码'):compile_samples(p)


def test_chunk_boundary_plateau_and_preview_boundaries():
    p=Profile(mode='function',initial=512,amplitude=112,rise_ms=.00002,hold_ms=2,fall_ms=.00003)
    r=compile_samples(p)
    assert r.total==400010
    assert any(code==112 and count>=400000 for code,count in r.runs)
    times,_,_,_=preview_samples(p)
    ticks=np.rint(times*200000).astype(int)
    assert {3,4,400003,400004}.issubset(set(ticks))


def test_old_whole_function_json_is_not_silently_reinterpreted():
    p=Profile(mode='function',function_layout='single',function='where(t<T/2,0,1023)',function_ms=.00002,initial=512)
    data=p.to_dict()
    for key in ('function_layout','rise_function','fall_function'):data.pop(key)
    restored=Profile.from_dict(data)
    assert restored.function_layout=='single'
    assert compile_samples(restored).runs==((0,2),(1023,2))


def test_gui_staged_gaussian_save_upload_trigger(tmp_path):
    import os
    os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
    from PySide6.QtWidgets import QApplication
    from PySide6.QtTest import QTest
    from host.app import Studio
    from host.storage import Library
    from tests.test_gui import until
    app=QApplication.instance() or QApplication([])
    w=Studio(Library(tmp_path)); w.resize(1440,1100); w.show(); app.processEvents()
    try:
        w.mode.setCurrentIndex(w.mode.findData('function')); w.apply_function_template('高斯')
        w.initial.setValue(512); w.amplitude.setValue(112)
        w.rise_ms.setValue(.01); w.hold_ms.setValue(.02); w.fall_ms.setValue(.015)
        assert w.ramp_panel.isVisible() and w.staged_function_panel.isVisible() and w.single_function_panel.isHidden()
        assert w.profile.function_layout=='staged' and w.profile_valid
        assert w.save_profile()
        loaded=Profile.from_dict(__import__('json').loads((tmp_path/'profiles'/f'{w.profile.id}.json').read_text('utf-8')))
        assert loaded.hold_ms==.02 and loaded.rise_function==w.rise_function.text()
        w.connect_button.click(); until(app,lambda:w.connected and w.last_status is not None)
        w.upload_button.click(); until(app,lambda:not w.busy and w.uploaded_id is not None,timeout=30)
        w.fall_function.setText(STAGED_TEMPLATES['线性'][1]); assert not w.arm_button.isEnabled()
        w.apply_function_template('高斯'); assert w.arm_button.isEnabled()
        w.arm_button.click(); until(app,lambda:not w.busy and w.last_status['state']==1)
        w.external_button.click(); until(app,lambda:not w.busy and w.last_status['state']==1 and w.last_status['loops']==1)
        assert w.last_status['code']==512
        w.connect_button.click(); until(app,lambda:not w.connected and not w.busy)
        QTest.qWait(250)
        from pathlib import Path
        w.grab().save(str(Path(__file__).resolve().parents[1]/'build/staged_gaussian.png'))
    finally:
        w.saved_fingerprint=fingerprint(w.profile)
        if w.connected:
            w.send('disconnect',None); until(app,lambda:not w.connected and not w.busy)
        w.close(); app.processEvents()
