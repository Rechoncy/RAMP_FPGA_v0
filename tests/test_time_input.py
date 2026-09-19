import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
import pytest
from PySide6.QtWidgets import QApplication
from decimal import Decimal
from host.time_input import TimeInput,MS_PER_UNIT
from host.model import fingerprint
from host.functions import compile_samples


@pytest.mark.parametrize('value',[0.,70.,.000005,.123456789012,1000.])
def test_unit_switch_keeps_number_and_changes_duration(value):
    app=QApplication.instance() or QApplication([])
    w=TimeInput(); w.setValue(value); changes=[]; w.valueChanged.connect(changes.append)
    previous=value;expected_changes=[]
    for _ in range(3):
        for unit in ('s','ns','µs','ms'):
            w.setUnit(unit)
            expected=float(Decimal(str(value))*MS_PER_UNIT[unit])
            assert w.spin.value()==value
            assert w.value()==expected
            if expected!=previous:expected_changes.append(expected)
            previous=expected
    assert changes==expected_changes
    w.close()


def test_input_conversions_ranges_and_microsecond_base():
    app=QApplication.instance() or QApplication([])
    w=TimeInput()
    for unit,number,expected in [('s',.07,70),('µs',70,.07),('ns',5,.000005),('ms',100,100)]:
        w.setUnit(unit); w.spin.setValue(number); assert w.value()==expected
    w.setUnit('s'); assert w.spin.value()==100 and w.value()==100_000
    assert w.spin.maximum()>=100  # Never silently clamp 100 s to 1 s.
    w.setValue(0);assert w.spin.maximum()==1
    w.setUnit('ns'); assert w.spin.maximum()==1_000_000_000
    interval=TimeInput(base_unit='µs',minimum=.08,maximum=327.675)
    interval.setUnit('ns'); interval.spin.setValue(85)
    assert interval.value()==.085
    w.close(); interval.close()


def test_restoring_units_preserves_saved_duration_and_uncommitted_input():
    app=QApplication.instance() or QApplication([])
    w=TimeInput();w.setValue(.123456789);changes=[];w.valueChanged.connect(changes.append)
    for unit in ('s','ns','µs','ms'):w.setUnit(unit,preserve_duration=True)
    assert w.value()==.123456789 and changes==[]
    w.setValue(70);changes.clear();w.spin.lineEdit().setText('80.123456789012')
    w.unit.setCurrentIndex(w.unit.findData('µs'))
    assert w.spin.value()==80.123456789012
    assert w.value()==.080123456789012 and changes==[.080123456789012]
    w.close()


def test_gui_mixed_units_change_requires_download_and_restores_invalid_draft(tmp_path):
    from host.app import Studio
    from host.storage import Library
    from tests.test_gui import until
    from PySide6.QtTest import QTest
    from PySide6.QtCore import QSettings
    from pathlib import Path
    app=QApplication.instance() or QApplication([])
    w=Studio(Library(tmp_path)); w.settings=QSettings(str(tmp_path/'units.ini'),QSettings.IniFormat)
    w.resize(1500,1100); w.show(); app.processEvents()
    try:
        w.rise_ms.setUnit('µs'); w.rise_ms.spin.setValue(10)
        w.hold_ms.setUnit('ms'); w.hold_ms.spin.setValue(.02)
        w.fall_ms.setUnit('ns'); w.fall_ms.spin.setValue(15000)
        assert (w.profile.rise_ms,w.profile.hold_ms,w.profile.fall_ms)==(.01,.02,.015)
        before=fingerprint(w.profile); samples=compile_samples(w.profile)
        assert w.save_profile()
        w.connect_button.click(); until(app,lambda:w.connected and w.last_status is not None)
        w.upload_button.click(); until(app,lambda:not w.busy and w.uploaded_id is not None)
        assert w.arm_button.isEnabled()
        identity=w.uploaded_id;w.rise_ms.setUnit('ms')
        assert w.rise_ms.spin.value()==10 and w.profile.rise_ms==10
        assert fingerprint(w.profile)!=before and compile_samples(w.profile)!=samples
        assert not w.arm_button.isEnabled() and w.save_state.text()=='未保存修改'
        assert w.uploaded_id==identity and w.last_status['config_id']==identity
        assert w.save_profile();w.upload_button.click();until(app,lambda:not w.busy and w.uploaded_id!=identity)
        assert w.arm_button.isEnabled()
        w.settings.sync(); assert w.settings.value('time_units/rise')=='ms'
        recreated=w.time_editor('rise'); recreated.setValue(.01)
        assert recreated.unit.currentData()=='ms' and recreated.spin.value()==.01
        w.rise_ms.setValue(70);w.rise_ms.setUnit('s')
        assert w.rise_ms.spin.value()==70 and w.rise_ms.value()==70000
        assert not w.profile_valid and not w.upload_button.isEnabled() and not w.arm_button.isEnabled()
        assert '时长' in w.validation.text()
        QTest.qWait(100)
    finally:
        w.saved_fingerprint=fingerprint(w.profile)
        if w.connected:w.send('disconnect',None); until(app,lambda:not w.connected and not w.busy)
        w.close(); app.processEvents()
    restored=Studio(Library(tmp_path))
    try:
        assert restored.rise_ms.unit.currentData()=='s'
        assert restored.rise_ms.spin.value()==70 and restored.rise_ms.value()==70000
        assert not restored.profile_valid
    finally:restored.close();app.processEvents()


def test_sequence_start_and_delay_units_update_saved_times(tmp_path):
    from host.app import Studio
    from host.storage import Library
    from tests.test_sequences import example
    app=QApplication.instance() or QApplication([]);w=Studio(Library(tmp_path));page=w.sequence_page
    try:
        p=example();page.load(p,False);page.save();page.table.selectRow(1)
        page.time.setUnit('ns',preserve_duration=True);assert page.time.spin.value()==100
        page.time.unit.setCurrentIndex(page.time.unit.findData('µs'))
        assert page.time.spin.value()==100 and page.collect().sequence_steps[1]['start_ms']==.1
        assert '重叠' in page.summary.text()
        page.save();assert '重叠' in page.feedback.text()
        assert next(x for x in w.library.list() if x.id==p.id).sequence_steps[1]['start_ms']==.0001
        page.time.setUnit('ns');assert page.time.spin.value()==100
        assert page.collect().sequence_steps[1]['start_ms']==.0001
        page.load(example(events=True),False);page.table.selectRow(2)
        page.kind.setCurrentIndex(page.kind.findData('delay'))
        page.time.setUnit('µs');page.time.spin.setValue(200)
        assert page.collect().sequence_steps[2]['delay_ms']==.2
        page.time.setUnit('ms');assert page.time.spin.value()==200
        page.save();saved=next(x for x in w.library.list() if x.id==page.profile.id)
        assert saved.sequence_steps[2]['delay_ms']==200
        page.load(saved);page.table.selectRow(2)
        assert page.time.spin.value()==200 and page.time.value()==200
    finally:w.close();app.processEvents()
