import os
os.environ.setdefault("QT_QPA_PLATFORM","offscreen")
import time
from pathlib import Path
from PySide6.QtWidgets import QApplication
from PySide6.QtTest import QTest
from host.app import Studio
from host.storage import Library
from host.model import fingerprint


def until(app,predicate,timeout=5):
    deadline=time.monotonic()+timeout
    while not predicate():
        if time.monotonic()>deadline: raise AssertionError("Qt operation timed out")
        app.processEvents(); QTest.qWait(20)
    app.processEvents()


def test_desktop_demo_upload_edit_guard_and_board_preview(tmp_path,monkeypatch):
    from PySide6.QtWidgets import QMessageBox
    errors=[]
    monkeypatch.setattr(QMessageBox,'warning',lambda *args:errors.append(str(args[2])))
    app=QApplication.instance() or QApplication([])
    app.setStyle("Fusion")
    w=Studio(Library(tmp_path)); w.resize(1440,1120); w.show(); app.processEvents()
    try:
        assert w.save_state.text()=="已保存"
        assert w.mode.currentData()=='ramp' and w.legacy_editor.isHidden()
        assert w.initial.isVisible() and w.safe.isVisible()
        w.rise_ms.setValue(2); w.hold_ms.setValue(3); w.fall_ms.setValue(4)
        w.connect_button.click()
        until(app,lambda:w.connected and w.last_status is not None)
        assert w.demo and w.upload_button.isEnabled()
        w.upload_button.click(); until(app,lambda:w.uploaded_id is not None and not w.busy,timeout=30)
        assert w.arm_button.isEnabled()
        w.amplitude.setValue(113); app.processEvents()
        assert not w.arm_button.isEnabled()
        w.amplitude.setValue(112); app.processEvents()
        w.arm_button.click(); until(app,lambda:w.last_status["state"]==1 and not w.busy)
        assert w.external_button.isEnabled()
        w.external_button.click(); until(app,lambda:not w.busy and w.last_status["state"]==1 and w.last_status["loops"]==1)
        root=Path(__file__).resolve().parents[1]/"build"
        root.mkdir(exist_ok=True)
        assert w.grab().save(str(root/"ramp_studio_workbench.png"))
        w.disarm_button.click(); until(app,lambda:not w.busy and w.last_status['state']==0)
        w.mode.setCurrentIndex(w.mode.findData('function')); w.function_layout.setCurrentIndex(w.function_layout.findData('single')); w.channel.setCurrentIndex(1)
        w.function.setText('where(t<T/2,0,1023)'); w.function_ms.setValue(.001)
        assert w.function_panel.isVisible() and w.profile.channel==1
        assert 'dac_data[19:10]' in w.route_label.text() and 'trigger_in[0]' in w.route_label.text()
        w.compile_button.click(); until(app,lambda:not w.busy,timeout=30)
        assert '200' in w.validation.text()
        w.upload_button.click(); until(app,lambda:not w.busy and w.uploaded_id is not None,timeout=30)
        assert w.arm_button.isEnabled()
        w.arm_button.click(); until(app,lambda:w.last_status['state']==1 and not w.busy)
        w.external_button.click(); until(app,lambda:not w.busy and w.last_status['state']==1 and w.last_status['loops']==1)
        assert w.last_status['channel']==1 and w.last_status['code']==512
        assert not errors,errors
        assert w.grab().save(str(root/'ramp_studio_function.png'))
        w.navigate(1); app.processEvents()
        assert w.grab().save(str(root/"ramp_studio_hardware.png"))
        w.board_page.preview()
        assert "uart_rx" in w.board_page.check_label.text()
        assert "200 MHz DAC" in w.hardware_summary.text()
        w.saved_fingerprint=fingerprint(w.profile)
        w.send("disconnect",None); until(app,lambda:not w.connected and not w.busy)
    finally:
        w.saved_fingerprint=fingerprint(w.profile)
        if w.connected:
            w.send("disconnect",None); until(app,lambda:not w.connected)
        w.close(); app.processEvents()


def test_initial_trigger_and_hardware_expansion_roundtrip(tmp_path,monkeypatch):
    import json
    from host.board import Board
    from PySide6.QtWidgets import QMessageBox
    errors=[]
    monkeypatch.setattr(QMessageBox,'warning',lambda *args:errors.append(str(args[2])))
    app=QApplication.instance() or QApplication([])
    w=Studio(Library(tmp_path)); w.resize(1440,1200); w.show(); app.processEvents()
    try:
        b=w.board_page; b.dac_count.setValue(4); b.trigger_count.setValue(3)
        assert w.channel.count()==4 and w.trigger_input.count()==3
        def row_for(port): return next(i for i in range(b.table.rowCount()) if b.table.item(i,0).text()==port)
        b.table.item(row_for('dac_data[20]'),1).setText('C3')  # syntax-only placeholder
        b.timing_table.item(2,1).setText('-1.1'); b.timing_table.item(2,2).setText('2.2'); b.timing_table.cellWidget(2,3).setCurrentIndex(2)
        b.dac_count.setValue(2); b.dac_count.setValue(4)
        assert b.table.item(row_for('dac_data[20]'),1).text()=='C3'
        assert b.timing_table.item(2,1).text()=='-1.1'
        b.save()
        saved=Board.from_dict(json.loads((tmp_path/'board.json').read_text(encoding='utf-8')))
        assert (saved.dac_count,saved.trigger_count)==(4,3) and saved.dac_timing['2']['falling']
        w.channel.setCurrentIndex(2); w.trigger_input.setCurrentIndex(0); w.trigger_edge.setCurrentIndex(1)
        w.initial.setValue(300); w.safe.setValue(444); w.amplitude.setValue(700)
        w.rise_ms.setValue(.01); w.hold_ms.setValue(.01); w.fall_ms.setValue(.01)
        assert w.profile.initial==300 and w.profile.safe==444 and w.profile.channel==2
        assert w.profile.trigger_input==0 and w.profile.trigger_edge=='falling'
        b.dac_count.setValue(2)
        assert w.channel.currentData()==2 and not w.profile_valid  # never silently retarget another DAC
        b.dac_count.setValue(4); assert w.profile_valid
        w.connect_button.click(); until(app,lambda:w.connected and w.last_status is not None)
        assert w.caps.dac_count==4 and w.caps.trigger_count==3
        w.upload_button.click(); until(app,lambda:not w.busy and w.uploaded_id is not None,timeout=30)
        assert w.last_status['code']==300
        w.initial.setValue(301); assert not w.arm_button.isEnabled()
        w.initial.setValue(300); assert w.arm_button.isEnabled()
        w.arm_button.click(); until(app,lambda:not w.busy and w.last_status['state']==1)
        w.external_button.click(); until(app,lambda:not w.busy and w.last_status['state']==1 and w.last_status['loops']==1)
        assert w.last_status['code']==300 and w.last_status['channel']==2
        assert w.last_status['trigger_input']==0 and w.last_status['trigger_edge']=='falling'
        w.abort_button.click(); until(app,lambda:not w.busy and w.last_status['state']==0)
        assert w.last_status['code']==444
        root=Path(__file__).resolve().parents[1]/'build'
        w.grab().save(str(root/'ramp_studio_expansion.png'))
        w.navigate(1); b.tabs.setCurrentWidget(b.info_page); app.processEvents(); w.grab().save(str(root/'ramp_studio_expansion_board.png'))
        b.tabs.setCurrentWidget(b.trigger_page); b.update_trigger_wiring(); app.processEvents(); w.grab().save(str(root/'ramp_studio_trigger_help.png'))
        assert 'F13' in b.trigger_wiring.text() and not errors,errors
    finally:
        w.saved_fingerprint=fingerprint(w.profile)
        if w.connected:
            w.send('disconnect',None); until(app,lambda:not w.connected and not w.busy)
        w.close(); app.processEvents()


def test_dac_width_ui_pin_identity_and_demo(tmp_path,monkeypatch):
    import json
    from host.board import Board
    from PySide6.QtWidgets import QMessageBox
    errors=[]
    monkeypatch.setattr(QMessageBox,'warning',lambda *args:errors.append(str(args[2])))
    app=QApplication.instance() or QApplication([])
    w=Studio(Library(tmp_path)); w.resize(1500,1200); w.show(); app.processEvents()
    try:
        b=w.board_page
        def row(port): return next(i for i in range(b.table.rowCount()) if b.table.item(i,0).text()==port)
        pin=b.table.item(row('dac_data[10]'),1).text()
        b.dac_bits.setCurrentIndex(b.dac_bits.findData(14))
        assert w.caps.dac_bits==14 and w.design_bits.currentData()==10
        # Hardware selection no longer rewrites existing experimental profiles.
        w.design_bits.setCurrentIndex(w.design_bits.findData(14))
        assert w.initial.maximum()==16383
        assert b.table.item(row('dac_data[14]'),1).text()==pin
        assert b.table.item(row('dac_data[10]'),1).text()==''
        b.table.item(row('dac_data[13]'),1).setText('A3')  # syntax-only placeholder
        b.dac_bits.setCurrentIndex(b.dac_bits.findData(10))
        assert b.table.item(row('dac_data[10]'),1).text()==pin
        b.dac_bits.setCurrentIndex(b.dac_bits.findData(14))
        assert b.table.item(row('dac_data[13]'),1).text()=='A3'
        b.save(); saved=Board.from_dict(json.loads((tmp_path/'board.json').read_text('utf-8')))
        assert saved.dac_bits==14 and saved.pins['dac_data[14]']==pin
        w.mode.setCurrentIndex(w.mode.findData('function')); w.function_layout.setCurrentIndex(w.function_layout.findData('single')); w.channel.setCurrentIndex(1)
        w.initial.setValue(8192); w.safe.setValue(16383); w.function.setText('I + 400*sin(2*pi*t/T)')
        assert w.profile_valid and w.profile.dac_bits==14 and 'dac_data[27:14]' in w.route_label.text()
        b.dac_bits.setCurrentIndex(b.dac_bits.findData(10))
        assert w.initial.value()==8192 and w.safe.value()==16383 and w.profile_valid
        from host.resource_usage import assess_resources
        assert not assess_resources(w.collect(),w.resource_caps()).fits
        assert w.profile.dac_bits==14
        b.dac_bits.setCurrentIndex(b.dac_bits.findData(14)); assert w.profile_valid
        w.save_profile(); assert next(p for p in w.library.list() if p.id==w.profile.id).dac_bits==14
        w.connect_button.click(); until(app,lambda:w.connected and w.last_status is not None)
        assert w.caps.dac_bits==14 and w.last_status['code']==8192
        # Board editing cannot change the connected device's real width.
        b.dac_bits.setCurrentIndex(b.dac_bits.findData(12)); assert w.caps.dac_bits==14
        b.dac_bits.setCurrentIndex(b.dac_bits.findData(14))
        w.upload_button.click(); until(app,lambda:not w.busy and w.uploaded_id is not None,timeout=30)
        w.arm_button.click(); until(app,lambda:not w.busy and w.last_status['state']==1)
        w.external_button.click(); until(app,lambda:not w.busy and w.last_status['state']==1 and w.last_status['loops']==1)
        assert w.last_status['code']==8192
        w.abort_button.click(); until(app,lambda:not w.busy and w.last_status['code']==16383)
        root=Path(__file__).resolve().parents[1]/'build'
        w.grab().save(str(root/'ramp_studio_14bit.png'))
        w.navigate(1); b.tabs.setCurrentWidget(b.info_page); app.processEvents(); w.grab().save(str(root/'ramp_studio_14bit_board.png'))
        assert not errors,errors
    finally:
        w.saved_fingerprint=fingerprint(w.profile)
        if w.connected:
            w.send('disconnect',None); until(app,lambda:not w.connected and not w.busy)
        w.close(); app.processEvents()
