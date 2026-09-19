from pathlib import Path
import sys
import pytest
from host import runtime
from host.storage import Library


def test_portable_library_survives_relocation(tmp_path, monkeypatch):
    import shutil
    monkeypatch.delenv('RAMP_STUDIO_DATA_DIR', raising=False)
    monkeypatch.setattr(sys, 'frozen', True, raising=False)
    first=tmp_path/'first location'; first.mkdir()
    monkeypatch.setattr(sys, 'executable', str(first/'Ramp Studio.exe'))
    library=Library()
    profile=library.list()[0]; profile.name='Migrated experiment'; library.save(profile)
    second=tmp_path/'second location'
    shutil.copytree(first,second)
    monkeypatch.setattr(sys, 'executable', str(second/'Ramp Studio.exe'))
    moved=Library()
    assert moved.root==second/'user_data'
    assert any(p.id==profile.id and p.name=='Migrated experiment' for p in moved.list())


def test_readonly_install_falls_back_but_existing_library_never_disappears(tmp_path,monkeypatch):
    monkeypatch.delenv('RAMP_STUDIO_DATA_DIR',raising=False)
    monkeypatch.setattr(sys,'frozen',True,raising=False)
    monkeypatch.setattr(sys,'executable',str(tmp_path/'app'/'Ramp Studio.exe'))
    monkeypatch.setenv('LOCALAPPDATA',str(tmp_path/'local'))
    def denied(*a,**kw): raise PermissionError('read only')
    monkeypatch.setattr(runtime.tempfile,'TemporaryFile',denied)
    assert runtime.data_dir()==tmp_path/'local'/'RampStudio'/'user_data'
    assert runtime.data_dir()==tmp_path/'local'/'RampStudio'/'user_data'
    (tmp_path/'app'/'user_data').mkdir()
    with pytest.raises(PermissionError,match='user_data'):
        runtime.data_dir()


def test_staged_firmware_is_independent_of_extraction_directory(tmp_path,monkeypatch):
    import shutil
    extracted=tmp_path/'temporary_bundle'
    for name in ('rtl/common/sample_engine.v','scripts/vivado/build_studio.tcl'):
        p=extracted/name; p.parent.mkdir(parents=True,exist_ok=True); p.write_text('test')
    monkeypatch.setattr(sys,'frozen',True,raising=False)
    monkeypatch.setattr(runtime,'resource_root',lambda:extracted)
    output=tmp_path/'saved_builds'/'project'
    script=runtime.build_script(output)
    shutil.rmtree(extracted)
    assert script.is_file()
    assert (script.parents[2]/'rtl/common/sample_engine.v').is_file()
    assert not output.exists()  # Vivado must create the output project directory itself.
    with pytest.raises(FileExistsError): runtime.build_script(output)
