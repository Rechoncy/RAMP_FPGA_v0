"""Visible, asynchronous capacity checks follow the current draft and device."""
import threading
from dataclasses import replace
from pathlib import Path
from PySide6.QtCore import QPoint
from PySide6.QtWidgets import QApplication
from PySide6.QtTest import QTest
from host.protocol import Device
from host.parallel_simulator import ParallelSimulator
from host.app import Studio
from host.model import Capabilities,fingerprint
from host.resource_monitor import ResourceMonitor
from host.resource_usage import assess_resources
from host.storage import Library
from tests.test_gui import until
from tests.test_resource_backend import plan,event_plan


def test_inflight_result_cannot_replace_new_draft_or_new_device(monkeypatch):
    app=QApplication.instance() or QApplication([])
    entered=threading.Event();release=threading.Event();calls=[]
    def check(p,caps):
        calls.append((fingerprint(p),caps))
        if len(calls)==1:entered.set();release.wait(5)
        return assess_resources(p,caps)
    monkeypatch.setattr('host.resource_monitor.assess_resources',check)
    monitor=ResourceMonitor();p=plan();q=replace(p,amplitude=510)
    old=Capabilities(max_runs=100);new=replace(old,max_runs=2)
    try:
        assert monitor.request('draft',p,old) is None
        monitor.timer.stop();monitor.start_next();assert entered.wait(2)
        assert monitor.request('draft',q,new) is None
        release.set()
        until(app,lambda:(fingerprint(q),new) in monitor.cache)
        report=monitor.request('draft',q,new)
        assert report.profile_fingerprint==fingerprint(q) and report.max_runs==2 and not report.fits
        assert len(calls)==2
        # Removing an invalid draft clears the displayed scope even with cached successes.
        assert monitor.request('draft',None,new) is None and 'draft' not in monitor.wanted
    finally:release.set();monitor.close()


def test_actual_capabilities_update_designers_and_block_only_uploads(tmp_path):
    app=QApplication.instance() or QApplication([]);lib=Library(tmp_path)
    p=plan();lib.save(p);w=Studio(lib)
    try:
        w.board_page.dac_count.setValue(4);w.refresh_hardware_options()
        assert w.resource_caps().max_runs==2048 and '离线预估' in w.capacity_summary.text()
        # Simulate the result of INFO, without opening a serial transport.
        caps=Capabilities(max_runs=2,dac_count=4,trigger_count=2,parallel_channels=True,sequence_steps=1,trigger_scan=True)
        w.connected=True;w.demo=False;w.on_capabilities(caps)
        device=Device(ParallelSimulator());w.channel_statuses={i:device.status(i) for i in range(4)};device.close()
        w.load_profile(p);w.open_dac(0)
        w.channel_page.plan.setCurrentIndex(w.channel_page.plan.findData(p.id))
        w.update_buttons()
        until(app,lambda:'超过设备' in w.channel_page.resource_view.label.text())
        assert '设备回报' in w.capacity_summary.text() and '每路 2 个' in w.capacity_summary.text()
        assert not w.channel_page.buttons['prepare'].isEnabled() and not w.channel_page.buttons['upload'].isEnabled()
        assert w.channel_page.buttons['abort'].isEnabled()
        assert not w.parallel_page.prepare.isEnabled()
        assert '超过设备' in w.parallel_page.rows[0]['resource'].text()
        assert '超过设备' in w.resource_view.label.text() and not w.upload_button.isEnabled()
        w.sequence_page.load(event_plan(3),False)
        until(app,lambda:'3 / 1' in w.sequence_page.resource_view.label.text())
        assert '超过设备' in w.sequence_page.resource_view.label.text()
        # A refresh takes effect without changing or borrowing from the board template.
        w.on_capabilities(replace(caps,max_runs=2048,sequence_steps=64))
        until(app,lambda:'容量通过' in w.channel_page.resource_view.label.text())
        w.update_buttons();assert w.channel_page.buttons['prepare'].isEnabled()
        assert '2048' not in w.capacity_summary.text() and '2,048' in w.capacity_summary.text()
        w.amplitude.setMaximum(9999);w.amplitude.setValue(9999)
        assert not w.profile_valid and '参数待修正' in w.resource_view.label.text()
        w.on_capabilities(replace(caps,max_runs=2048,sequence_steps=64,trigger_scan=False))
        until(app,lambda:'不支持' in w.scan_page.resource_view.label.text() or '升级' in w.scan_page.resource_view.label.text())
    finally:
        w.connected=False;w.close();until(app,lambda:not w.thread.isRunning())


def test_eight_dac_layout_and_local_resource_labels(tmp_path):
    app=QApplication.instance() or QApplication([]);w=Studio(Library(tmp_path))
    stage=Path(__file__).resolve().parents[1]/'build/resource_capacity_release';stage.mkdir(exist_ok=True)
    try:
        w.board_page.dac_count.setValue(8);w.refresh_hardware_options();w.resize(1080,760);w.show()
        assert w.resource_caps().max_runs==1024
        w.navigate(4);QTest.qWait(300);app.processEvents()
        for row in w.parallel_page.rows:
            resource=row['resource']
            assert resource.width()>100
            assert '离线预估' in resource.text() and '选择方案' in resource.text()
            assert resource.mapTo(row['frame'],QPoint(0,resource.height())).y()<=row['frame'].height()
        assert w.grab().save(str(stage/'output-eight-dac.png'))
        w.navigate(1);QTest.qWait(300);app.processEvents()
        assert not w.capacity_refresh.isEnabled()
        assert w.grab().save(str(stage/'board-capabilities.png'))
        w.load_profile(plan());w.navigate(0)
        until(app,lambda:'容量通过' in w.resource_view.label.text())
        QTest.qWait(300);w.designer_tabs.widget(0).ensureWidgetVisible(w.resource_view);app.processEvents()
        assert w.grab().save(str(stage/'designer-resources.png'))
    finally:w.close();until(app,lambda:not w.thread.isRunning())


def test_offline_full_check_does_not_claim_overflow_passed(tmp_path):
    from host.functions import compile_samples
    app=QApplication.instance() or QApplication([]);w=Studio(Library(tmp_path))
    try:
        w.board_page.dac_count.setValue(4)
        w.board_page.dac_bits.setCurrentIndex(w.board_page.dac_bits.findData(14));w.refresh_hardware_options()
        p=plan(dac_bits=14,initial=8192,safe=8192,amplitude=6692,rise_ms=.02,fall_ms=.02)
        w.load_profile(p);w.compiling_fingerprint=fingerprint(w.profile)
        compiled=compile_samples(w.profile);assert len(compiled.runs)>2048
        w.worker_result('compile',compiled)
        assert '离线预估' in w.validation.text() and '容量超限' in w.validation.text()
        assert '完整离散通过' not in w.validation.text()
    finally:w.close();until(app,lambda:not w.thread.isRunning())
