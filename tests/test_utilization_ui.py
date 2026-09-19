"""Physical resource views keep report provenance distinct from device telemetry."""
import json,shutil
from pathlib import Path
from dataclasses import replace
from PySide6.QtCore import QProcess,Qt
from PySide6.QtWidgets import QApplication
from PySide6.QtTest import QTest
from host.app import Studio
from host.board import Board
from host.storage import Library
from host.utilization import write_build_manifest,board_fingerprint
from host.utilization_panel import UtilizationPanel
from tests.test_gui import until

ROOT=Path(__file__).resolve().parents[1]
REFERENCE=ROOT/'host/assets/reference-firmware/trigger-scan'


def studio(tmp_path):
    app=QApplication.instance() or QApplication([]);app.setStyle('Fusion')
    lib=Library(tmp_path);board=Board.from_dict(json.loads((REFERENCE/'board.json').read_text(encoding='utf-8')))
    (tmp_path/'board.json').write_text(json.dumps(board.to_dict()),encoding='utf-8')
    return app,Studio(lib),board


def close(app,w):
    w.close();until(app,lambda:not w.thread.isRunning())


def test_reference_four_metrics_and_different_part_never_gets_reference_totals(tmp_path):
    app,w,b=studio(tmp_path)
    try:
        page=w.board_page;panel=page.utilization_panel
        assert panel.metrics['lut']['count'].text()=='7,803 / 20,800'
        assert panel.metrics['bram']['percent'].text()=='64.00%'
        assert w.hardware_resources.metrics['bram']['percent'].text()=='64.00%'
        assert panel.stage.text()=='布局布线' and '发布固件参考' in panel.origin.text()
        assert '串口确认' in panel.state.text()
        page.part.setText('xc7a100tcsg324-1')
        until(app,lambda:panel.metrics['lut']['percent'].text()=='—')
        assert panel.stage.text()=='暂无报告'
        assert w.hardware_resources.metrics['lut']['percent'].text()=='—'
        page.part.setText(b.part);until(app,lambda:panel.metrics['lut']['percent'].text()=='37.51%')
        page.utilization_panel.build_requested.emit()
        assert page.tabs.currentWidget()==page.build_page and page.mode.currentData()=='synth' and page.process is None
        assert w.stack.currentWidget()==page and not w.resource_dialog.isVisible()
    finally:close(app,w)


def test_imported_report_is_copied_and_survives_moved_user_data(tmp_path):
    app,w,b=studio(tmp_path/'first')
    try:
        panel=w.board_page.utilization_panel
        assert panel.import_report(REFERENCE/'utilization.rpt')
        record=json.loads(panel.selection_path.read_text(encoding='utf-8'))
        assert not Path(record['report']).is_absolute()
        assert '导入报告' in panel.origin.text() and '未关联当前工程参数' in panel.state.text()
        destination=tmp_path/'moved';shutil.copytree(panel.root,destination)
        moved=UtilizationPanel(destination);moved.set_board(b)
        assert Path(moved.report['path']).is_relative_to(destination)
        assert moved.metrics['ff']['percent'].text()=='25.99%'
        invalid=tmp_path/'bad.rpt';invalid.write_text('unrelated file')
        before=moved.report['sha256'];assert not moved.import_report(invalid)
        assert moved.report['sha256']==before and '导入未完成' in moved.state.text()
        Path(moved.report['path']).write_text('changed copy')
        reopened=UtilizationPanel(destination);reopened.set_board(b)
        assert reopened.report is None and '报告无法读取' in reopened.state.text()
        assert reopened.metrics['lut']['percent'].text()=='—';reopened.deleteLater()
        moved.deleteLater()
    finally:close(app,w)


def test_build_completion_updates_report_and_all_implementation_settings_mark_old(tmp_path,monkeypatch):
    app,w,b=studio(tmp_path)
    try:
        page=w.board_page;panel=page.utilization_panel
        built=page.collect();output=tmp_path/'builds/studio_test';output.mkdir(parents=True)
        shutil.copy2(REFERENCE/'utilization.rpt',output/'utilization_routed.rpt')
        write_build_manifest(output,built,'implement','success',ROOT)
        monkeypatch.setattr(page,'read_log',lambda:None)
        page.build_mode='synth';page.build_output=output
        page.build_finished(0,QProcess.NormalExit)
        assert panel.record['kind']=='build' and '配置与当前设置一致' in panel.state.text()
        assert panel.manifest['board_fingerprint']==board_fingerprint(built)
        page.name.setText('renamed');page.vivado.setText('D:/tools/vivado.bat')
        page.refresh_utilization_context();assert '配置与当前设置一致' in panel.state.text()
        page.dac_count.setValue(3)
        until(app,lambda:'旧配置' in panel.state.text())
        page.dac_count.setValue(built.dac_count);page.refresh_utilization_context()
        assert '配置与当前设置一致' in panel.state.text()
        edge=page.timing_table.cellWidget(0,3);original=edge.currentData()
        edge.setCurrentIndex(edge.findData(not bool(original)))
        until(app,lambda:'旧配置' in panel.state.text())
        edge.setCurrentIndex(edge.findData(original));page.refresh_utilization_context()
        assert '配置与当前设置一致' in panel.state.text()
        page.ref.setValue(100);until(app,lambda:'旧配置' in panel.state.text())
        page.ref.setValue(built.ref_mhz);page.refresh_utilization_context()
        assert '配置与当前设置一致' in panel.state.text()
        page.delay_max.setText('0.7');until(app,lambda:'旧配置' in panel.state.text())
        # Failed build still displays its synthesis report, with a visible failure.
        failed=tmp_path/'builds/studio_failed';failed.mkdir()
        shutil.copy2(REFERENCE/'utilization.rpt',failed/'utilization_synth.rpt')
        write_build_manifest(failed,built,'implement','failed',ROOT,error='timing failed')
        panel.refresh_latest();assert '未成功' in panel.state.text() and panel.stage.text()=='综合'
        # A hash mismatch must not leave the previous green metrics visible.
        (failed/'utilization_synth.rpt').write_text('modified report')
        panel.refresh_latest();assert panel.metrics['bram']['percent'].text()=='—'
        assert panel.manifest['warnings']
    finally:close(app,w)


def test_missing_fractional_and_over_limit_values_and_generate_retains_source(tmp_path):
    app,w,b=studio(tmp_path)
    try:
        panel=w.board_page.utilization_panel
        report=tmp_path/'partial.rpt'
        report.write_text('| Device : '+b.part+'\n| Design State : Synthesized\n| Site Type | Used | Available | Util% |\n| Slice LUTs | 25000 | 20800 | 120.19 |\n| Slice Registers | N/A | 41600 | N/A |\n| Block RAM Tile | 0.5 | 50 | 1.00 |\n| DSPs | 0 | 90 | <0.01 |\n')
        assert panel.import_report(report)
        assert panel.metrics['lut']['percent'].text()=='120.19%' and panel.metrics['lut']['bar'].value()==1000
        assert '#b43f48' in panel.metrics['lut']['percent'].styleSheet()
        assert w.hardware_resources.metrics['lut']['percent'].text()=='120.19%'
        assert '超限' in w.hardware_resources.state.text()
        assert panel.metrics['ff']['count'].text()=='— / 41,600' and panel.metrics['ff']['percent'].text()=='—'
        assert panel.metrics['bram']['count'].text()=='0.5 / 50'
        assert panel.metrics['dsp']['percent'].text()=='<0.01%'
        output=tmp_path/'builds/studio_generate';write_build_manifest(output,b,'generate','success',ROOT)
        panel.refresh_latest();assert panel.record['kind']=='import' and '没有新的资源统计' in panel.state.text()
    finally:close(app,w)


def test_resource_panel_layout_at_small_and_large_window(tmp_path):
    app,w,b=studio(tmp_path);stage=ROOT/'build/sidebar_resources_release';stage.mkdir(exist_ok=True)
    try:
        w.navigate(1);w.board_page.tabs.setCurrentWidget(w.board_page.info_page);w.show()
        for width,height in [(1440,1000),(1080,700)]:
            w.resize(width,height);QTest.qWait(300);app.processEvents()
            panel=w.board_page.utilization_panel
            assert w.board_page.info_page.horizontalScrollBar().maximum()==0
            compact=w.hardware_resources
            assert compact.isVisible() and not panel.isVisible()
            assert compact.geometry().bottom()<w.sidebar_footer.geometry().top()
            assert compact.minimumSizeHint().width()<=compact.width()
            assert compact.metrics['bram']['percent'].isVisible()
            assert w.grab().save(str(stage/f'board-resources-{width}.png'))
            for page in range(6):
                w.navigate(page);app.processEvents()
                assert compact.isVisible() and not panel.isVisible()
            QTest.mouseClick(compact.open_button,Qt.LeftButton);QTest.qWait(150)
            assert w.resource_dialog.isVisible() and panel.metrics['bram']['percent'].isVisible()
            assert w.resource_scroll.horizontalScrollBar().maximum()==0
            assert panel.metrics['lut']['count'].width()>100
            assert w.resource_dialog.grab().save(str(stage/f'resource-details-{width}.png'))
            QTest.keyClick(w.resource_dialog,Qt.Key_Escape);app.processEvents()
            assert not w.resource_dialog.isVisible() and w.isVisible()
            w.navigate(1);w.board_page.tabs.setCurrentWidget(w.board_page.info_page)
        w.board_page.dac_count.setValue(8);w.resize(1080,700);QTest.qWait(300)
        assert w.hardware_resources.isVisible() and w.side_status.isVisible()
        assert w.hardware_resources.geometry().bottom()<w.side_status.geometry().top()
    finally:close(app,w)
