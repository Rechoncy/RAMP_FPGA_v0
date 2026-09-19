"""Board drafts survive restart without applying unfinished hardware settings."""
import json
import os

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

import pytest
from PySide6.QtWidgets import QApplication
from host.board_page import BoardPage
from host.model import Profile
from host.storage import Library


@pytest.fixture
def page(tmp_path):
    app = QApplication.instance() or QApplication([])
    widget = BoardPage(Library(tmp_path))
    yield widget
    widget.close()
    app.processEvents()


def row(page, name):
    return next(i for i in range(page.table.rowCount()) if page.table.item(i, 0).text() == name)


def test_diagnostic_timing_is_explicit_saved_and_preserves_channel_overrides(page, tmp_path):
    assert page.collect().dac_delay_min is None
    page.timing_table.item(0, 1).setText('-0.8')
    page.timing_table.item(0, 2).setText('1.2')
    page.diagnostic_timing_button.click()
    board=page.collect()
    assert (board.dac_delay_min,board.dac_delay_max,board.dac_falling)==(-.5,1.,False)
    assert board.dac_timing['0']['min']==-.8 and board.dac_timing['0']['max']==1.2
    assert not (tmp_path/'board.json').exists()
    assert page.save()
    other=BoardPage(Library(tmp_path))
    try:
        restored=other.collect()
        assert (restored.dac_delay_min,restored.dac_delay_max,restored.dac_falling)==(-.5,1.,False)
        assert restored.dac_timing['0']==board.dac_timing['0']
    finally:other.close()


def test_raw_board_and_flash_drafts_restore_without_writes_or_actions(page, tmp_path):
    assert page.save()
    known_valid = (tmp_path / 'board.json').read_bytes()
    page.name.setText('未完成板卡草稿')
    page.part.setText('xc7a-')
    page.standard.setCurrentText('LVCMOS18')
    page.clock_input.setCurrentIndex(page.clock_input.findData('differential'))
    page.ref.setValue(100)
    page.clock_diff_term.setChecked(True)
    page.table.item(row(page, 'sys_clk_p'), 1).setText(' A? ')
    page.table.item(row(page, 'uart_rx'), 2).setText(' LVCMOS? ')
    page.delay_min.setText(' - ')
    page.timing_table.item(1, 2).setText('未确认')
    page.mode.setCurrentIndex(page.mode.findData('implement'))
    page.port_filter.setCurrentIndex(page.port_filter.findData('clock'))
    page.tabs.setCurrentWidget(page.flash_page)
    flash = page.flash_page
    flash.part.setCurrentText('我的 Flash 型号草稿')
    flash.interface.setCurrentText('SPIx4')
    flash.size.setCurrentIndex(flash.size.findData(32))
    flash.rate.setCurrentIndex(flash.rate.findData(33))
    flash.target.setCurrentText('自定义目标')
    flash.device.setCurrentText('自定义器件')
    flash.server.setText('tcp:127.0.0.1:3121')
    flash.bit.setText('D:/待选择/固件.bit')
    flash.expected.setText('not hex yet')
    flash.backup.setChecked(False)
    flash.boot_profiles = {'0': Profile(mode='ramp', name='上电方案').to_dict()}
    flash.boot_checks[0].setChecked(False)
    before = {p.name: p.read_bytes() for p in tmp_path.iterdir() if p.is_file()}
    snapshot = json.loads(json.dumps(page.capture_session()))
    assert page.valid_settings() is None

    other = BoardPage(Library(tmp_path))
    signals = []
    other.configuration_changed.connect(lambda: signals.append('configuration'))
    other.notification.connect(lambda value: signals.append(value))
    try:
        assert other.restore_session(snapshot)
        assert other.capture_session() == snapshot
        assert other.tabs.currentWidget() is other.flash_page
        other.utilization_panel.build_requested.emit()
        assert other.tabs.currentWidget() is other.build_page
        other.tabs.setCurrentWidget(other.flash_page)
        assert other.valid_settings() is None
        assert other.process is None and other.flash_page.process is None
        assert not signals
        assert (tmp_path / 'board.json').read_bytes() == known_valid
        assert {p.name: p.read_bytes() for p in tmp_path.iterdir() if p.is_file()} == before
    finally:
        other.close()


def test_inactive_dac_width_and_clock_settings_survive_session(page, tmp_path):
    page.dac_count.setValue(4)
    page.dac_bits.setCurrentIndex(page.dac_bits.findData(14))
    page.table.item(row(page, 'dac_data[55]'), 1).setText('AA1')
    page.table.item(row(page, 'dac_data[55]'), 2).setText('LVCMOS18')
    page.timing_table.item(3, 1).setText('-1.25')
    page.timing_table.item(3, 2).setText('2.5')
    page.timing_table.cellWidget(3, 3).setCurrentIndex(2)
    page.clock_io.setCurrentIndex(page.clock_io.findData('LVCMOS18'))
    page.clock_input.setCurrentIndex(page.clock_input.findData('differential'))
    page.clock_io.setCurrentIndex(page.clock_io.findData('LVDS'))
    page.table.item(row(page, 'sys_clk_p'), 1).setText('A1')
    page.table.item(row(page, 'sys_clk_n'), 1).setText('A2')
    page.clock_input.setCurrentIndex(page.clock_input.findData('single_ended'))
    page.dac_bits.setCurrentIndex(page.dac_bits.findData(10))
    page.dac_count.setValue(2)
    snapshot = json.loads(json.dumps(page.capture_session()))

    other = BoardPage(Library(tmp_path))
    try:
        assert other.restore_session(snapshot)
        other.dac_count.setValue(4)
        other.dac_bits.setCurrentIndex(other.dac_bits.findData(14))
        assert other.table.item(row(other, 'dac_data[55]'), 1).text() == 'AA1'
        assert other.table.item(row(other, 'dac_data[55]'), 2).text() == 'LVCMOS18'
        assert other.timing_table.item(3, 1).text() == '-1.25'
        assert other.timing_table.item(3, 2).text() == '2.5'
        assert other.timing_table.cellWidget(3, 3).currentData() is True
        assert other.clock_io.currentData() == 'LVCMOS18'
        other.clock_input.setCurrentIndex(other.clock_input.findData('differential'))
        assert other.clock_io.currentData() == 'LVDS'
        assert other.table.item(row(other, 'sys_clk_p'), 1).text() == 'A1'
        assert other.table.item(row(other, 'sys_clk_n'), 1).text() == 'A2'
    finally:
        other.close()


def test_valid_settings_and_intermediate_spin_text(page, tmp_path):
    page.ref.setValue(200)
    page.standard.setCurrentText('LVCMOS18')
    board = page.valid_settings()
    assert board.ref_mhz == 200 and board.io_standard == 'LVCMOS18'
    assert not (tmp_path / 'board.json').exists()
    page.ref.lineEdit().setText(' MHz')
    assert page.valid_settings() is None
    snapshot = json.loads(json.dumps(page.capture_session()))
    assert page.restore_session(snapshot)
    assert page.ref.lineEdit().text() == ' MHz'
    assert page.valid_settings() is None
    assert page.restore_session({'schema': 99}) is False
