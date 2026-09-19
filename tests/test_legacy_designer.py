"""The current waveform designer must never resurrect retired scan controls."""
import json
from dataclasses import replace
import pytest
from PySide6.QtWidgets import QApplication,QLabel,QInputDialog
from host.app import Studio
from host.model import Profile,Capabilities,Segment,compile_profile,fingerprint
from host.storage import Library
from host.legacy_profile import ramp_copy,draft_snapshot


@pytest.mark.parametrize('bits',[10,12,14])
@pytest.mark.parametrize('mode',['ramp','function','sequence'])
def test_scan_metadata_cannot_invalidate_current_waveforms(bits,mode):
    p=Profile(mode=mode,dac_bits=bits,initial=100,safe=100,fm_initial=-2,fm_step=100000,fm_points=0)
    if mode=='sequence':
        from tests.test_sequences import example
        p=replace(example(bits),dac_bits=bits,fm_initial=-2,fm_step=100000,fm_points=0)
    result=compile_profile(p,Capabilities(dac_bits=bits,sequence_steps=64))
    assert result.duration_ms>0
    assert Profile.from_dict(p.to_dict()).mode==mode
    with pytest.raises(ValueError,match='FM'):
        compile_profile(Profile(fm_initial=-2))


def test_ramp_conversion_preserves_parameters_and_never_changes_original():
    p=Profile(name='历史实验',initial=200,safe=190,repeat=3,trigger='software',segments=[Segment(50,7),Segment(50,12),Segment(200,9)])
    before=fingerprint(p);new=ramp_copy(p)
    assert new.id!=p.id and new.mode=='ramp' and new.amplitude==50
    assert (new.initial,new.safe,new.repeat,new.trigger)==(200,190,3,'software')
    assert (new.rise_ms,new.hold_ms,new.fall_ms)==(7,12,9)
    assert fingerprint(p)==before
    with pytest.raises(ValueError,match='单幅度三段'):
        ramp_copy(replace(p,repeat=1,segments=[Segment(51,7),Segment(50,12),Segment(200,9)]))


def test_saved_legacy_and_old_draft_never_reopen_scan_editor(tmp_path,monkeypatch):
    app=QApplication.instance() or QApplication([]);library=Library(tmp_path)
    p=Profile();library.save(p);source=library.profiles/(p.id+'.json');original=source.read_bytes()
    draft={'selected':0,'drafts':{'0':{'profile':p.to_dict(),'values':{'fm_initial':9,'fm_step':0,'fm_points':4},'combos':{'mode':'segments'}}}}
    draftpath=tmp_path/'designer_draft.json';draftpath.write_text(json.dumps(draft),encoding='utf-8');draftbytes=draftpath.read_bytes()
    w=Studio(library);w.show();app.processEvents()
    try:
        assert w.mode.currentData()=='ramp' and w.mode.count()==2 and w.legacy_editor.isHidden()
        assert not any(hasattr(w,k) for k in ('fm_initial','fm_step','fm_points'))
        assert draftpath.read_bytes()==draftbytes
        w.load_profile(p);app.processEvents()
        assert w.legacy_readonly and not w.save_button.isEnabled() and not w.upload_button.isEnabled()
        assert not any('FM' in x.text() for x in w.findChildren(QLabel) if x.isVisible())
        assert fingerprint(w.collect())==fingerprint(p)
        assert w.table.editTriggers()==w.table.EditTrigger.NoEditTriggers
        w.initial.setValue(350);assert w.collect().initial==p.initial
        w.convert_legacy_profile();assert not w.legacy_readonly and w.mode.count()==2
        assert w.profile.mode=='ramp' and w.profile.id!=p.id and w.save_state.text()=='未保存修改'
        assert source.read_bytes()==original and not (library.profiles/(w.profile.id+'.json')).exists()
        assert w.save_profile()
        assert source.read_bytes()==original
    finally:w.close();app.processEvents()
    copies=list((tmp_path/'archive').glob('legacy_designer_draft_*.json'))
    assert any(path.read_bytes()==draftbytes for path in copies)
    assert source.read_bytes()==original
    restored=Studio(library)
    try:
        assert restored.profile.mode=='ramp' and restored.mode.count()==2
        assert not restored.recover_draft_button.isHidden()
        monkeypatch.setattr(restored,'maybe_discard',lambda:True)
        monkeypatch.setattr(QInputDialog,'getItem',lambda *args:(args[3][0],True))
        restored.recover_legacy_draft()
        assert restored.legacy_readonly and restored.collect().fm_initial==9
    finally:restored.close();app.processEvents()


def test_explicit_legacy_draft_readback_keeps_unsaved_segment_edits(tmp_path):
    app=QApplication.instance() or QApplication([]);w=Studio(Library(tmp_path))
    p=Profile();draft={'profile':p.to_dict(),'texts':{'name':'历史草稿'},'values':{'initial':200,'safe':190},
        'table':[['01','900','3','上升'],['02','900','5','保持'],['03','200','7','下降']]}
    snapshot=draft_snapshot(draft)
    try:
        w.restore_dac_draft(0,draft)
        assert w.legacy_readonly and fingerprint(w.collect())==fingerprint(snapshot)
        w.convert_legacy_profile()
        assert w.profile.initial==200 and w.profile.amplitude==900 and w.profile.fall_ms==7
    finally:w.close();app.processEvents()
