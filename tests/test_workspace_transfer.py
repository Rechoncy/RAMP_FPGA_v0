"""Global transfer preserves editable work and reloads without device actions."""
import json
import os
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

import pytest
from PySide6.QtCore import QProcess, QSettings
from PySide6.QtWidgets import QApplication, QFileDialog, QMessageBox

from host.app import Studio
from host.model import Profile
from host.storage import Library
from host.workspace_archive import read_archive
from tests.test_gui import until


@pytest.fixture
def desktop(tmp_path, monkeypatch):
    QSettings.setDefaultFormat(QSettings.IniFormat)
    QSettings.setPath(QSettings.IniFormat, QSettings.UserScope, str(tmp_path / 'preferences'))
    app = QApplication.instance() or QApplication([])
    app.setStyle('Fusion')
    windows, warnings, commands = [], [], []
    monkeypatch.setattr(QMessageBox, 'warning', lambda *args: warnings.append(args[-1]))
    monkeypatch.setattr(QMessageBox, 'information', lambda *args: warnings.append(args[-1]))
    import serial
    def unexpected_serial(*args, **kwargs):
        raise AssertionError('Saving/importing must not open a physical serial port')
    monkeypatch.setattr(serial, 'Serial', unexpected_serial)

    def make(root=None):
        w = Studio(Library(root or tmp_path / 'data'))
        windows.append(w)
        w.request.connect(lambda *args: commands.append(args[0]))
        w.show()
        app.processEvents()
        return w

    def close(w):
        w.close()
        until(app, lambda: not w.thread.isRunning(), timeout=20)

    yield app, make, close, warnings, commands
    for w in windows:
        if w.thread.isRunning():
            close(w)


def saved_files(root):
    """Only files governed by configuration writes, excluding runtime history."""
    return {p.relative_to(root).as_posix(): p.read_bytes()
            for p in Path(root).rglob('*') if p.is_file()
            and p.suffix == '.json' and 'archive' not in p.relative_to(root).parts}


def test_reopening_initialized_empty_library_does_not_invent_profiles(tmp_path):
    root = tmp_path / '已清空的方案库'
    library = Library(root)
    assert library.list()
    for profile in library.list():
        library.archive(profile)
    assert library.list() == [] and (root / '.sample_profiles_added').exists()
    assert Library(root).list() == []


def test_global_roundtrip_preserves_raw_drafts_board_assignments_and_rollback(desktop, tmp_path):
    app, make, close, warnings, commands = desktop
    w = make()
    wave = Profile(mode='ramp', name='全局包中的有效方案', initial=512, safe=512,
                   amplitude=500, rise_ms=.001, hold_ms=.001, fall_ms=.001)
    w.library.save(wave)
    w.library.save(Profile(mode='scan', name='保存的触发步进', scan_start=100,
                           scan_step=2, scan_points=10))
    w.library.save(Profile(mode='sequence', name='保存的执行序列', sequence_steps=[
        dict(waveform=wave.to_dict(), kind='fixed', start_ms=0, delay_ms=0,
             input=0, edge='rising', repeat=1)]))
    w.refresh_library()
    board = w.board_page
    board.name.setText('全局迁移的四路板卡')
    board.dac_count.setValue(4)
    board.uart_baud.setCurrentIndex(board.uart_baud.findData(1_000_000))
    board.flash_page.bit.setText('D:/外部固件/启动.bit')
    board.flash_page.source.setCurrentIndex(board.flash_page.source.findData('image'))
    board.flash_page.image.setText('D:/外部固件/启动.mcs')
    board.flash_page.expected.setText('ABCDEF01')
    board.tabs.setCurrentIndex(1)
    w.baudrate.setCurrentIndex(w.baudrate.findData(1_000_000))
    w.ports.addItem('测试串口', 'COM987')
    w.ports.setCurrentIndex(w.ports.findData('COM987'))
    w.open_dac(3)
    cp = w.channel_page
    cp.plan.setCurrentIndex(cp.plan.findData(wave.id))
    cp.view.setCurrentIndex(cp.view.findData('local'))
    w.parallel_page.store.override(3, dict(trigger='external', trigger_input=1,
                                        trigger_edge='falling'))
    w.debug_page.channel.setCurrentIndex(w.debug_page.channel.findData(2))
    w.debug_page.code.setValue(37)
    w.debug_page.safe.setValue(511)
    w.load_profile(wave)
    w.name.setText('还未保存的非法函数草稿')
    w.mode.setCurrentIndex(w.mode.findData('function'))
    w.rise_function.setText('exp(')
    w.repeat.setValue(29)
    w.repeat_mode.setCurrentIndex(w.repeat_mode.findData('infinite'))
    w.rise_ms.setUnit('µs', preserve_duration=True)
    w.rise_ms.spin.lineEdit().setText('17.25')
    assert w.rise_ms.value() != .01725
    original_profiles = {p.name: p.read_bytes() for p in w.library.profiles.glob('*.json')}
    archive = tmp_path / '全局设置.ramp-workspace'
    summary = w.save_workspace(archive)
    assert summary['profile_count'] >= 3 and archive.exists()
    assert not w._exit_saved and w.thread.isRunning()
    assert w.rise_ms.value() == pytest.approx(.01725)
    assert not w.profile_valid and not commands
    assert {p.name: p.read_bytes() for p in w.library.profiles.glob('*.json')} == original_profiles

    # Import over different, unsaved edits. They must enter the rollback package
    # and must not overwrite the imported configuration during closeEvent.
    w.name.setText('导入前的另一份草稿')
    w.board_page.name.setText('导入前板卡')
    w.baudrate.setCurrentIndex(w.baudrate.findData(57600))
    signals = []
    w.workspace_reopen.connect(lambda *args: signals.append(args))
    backup = w.import_workspace(read_archive(archive))
    until(app, lambda: not w.thread.isRunning() and bool(signals), timeout=20)
    assert Path(backup).is_file() and signals == [(str(w.library.root), str(backup))]
    assert w._exit_saved and not w.isVisible() and not commands
    other = make(w.library.root)
    assert other.name.text() == '还未保存的非法函数草稿'
    assert other.rise_function.text() == 'exp(' and not other.profile_valid
    assert other.rise_ms.unit.currentData() == 'µs' and other.rise_ms.spin.value() == 17.25
    assert other.repeat_mode.currentData() == 'infinite' and other.repeat.value() == 29
    assert other.board_page.name.text() == '全局迁移的四路板卡'
    assert other.board_page.uart_baud.currentData() == other.baudrate.currentData() == 1_000_000
    assert other.board_page.flash_page.bit.text() == 'D:/外部固件/启动.bit'
    assert other.board_page.flash_page.source.currentData() == 'image'
    assert other.board_page.flash_page.image.text() == 'D:/外部固件/启动.mcs'
    assert other.board_page.flash_page.expected.text() == 'ABCDEF01'
    assert other.board_page.tabs.currentIndex() == 1
    assert other.ports.currentData() == 'COM987' and other.caps.dac_count == 4
    assert other.parallel_page.store.assignments['3']['trigger_override']['trigger_input'] == 1
    assert other.debug_page.channel.currentData() == 2 and other.debug_page.code.value() == 37
    assert not other.connected and not other.busy and not other.channel_statuses
    assert not other.parallel_page.store.identities and not other.debug_page.confirmed
    assert {p.name: p.read_bytes() for p in other.library.profiles.glob('*.json')} == original_profiles
    assert not commands and not warnings

    other.import_workspace(read_archive(backup))
    until(app, lambda: not other.thread.isRunning(), timeout=20)
    recovered = make(other.library.root)
    assert recovered.name.text() == '导入前的另一份草稿'
    assert recovered.board_page.name.text() == '导入前板卡'
    assert recovered.baudrate.currentData() == 57600 and not recovered.connected
    assert not commands and not warnings


@pytest.mark.parametrize('state', ['connected', 'busy', 'flash', 'build'])
def test_transfer_locks_before_any_file_write(desktop, tmp_path, monkeypatch, state):
    app, make, close, warnings, commands = desktop
    w = make()
    w.session.save(w)
    before = saved_files(w.library.root)
    w.name.setText('不能在锁定期间写出此草稿')
    old_process = w.board_page.process
    old_running = w.board_page.flash_page.running
    try:
        if state == 'connected': w.connected = True
        if state == 'busy': w.busy = True
        if state == 'flash': monkeypatch.setattr(w.board_page.flash_page, 'running', lambda: True)
        if state == 'build': w.board_page.process = SimpleNamespace(state=lambda: QProcess.Running)
        w.update_buttons()
        assert not w.workspace_import_button.isEnabled()
        assert w.workspace_save_button.isEnabled() == (state == 'connected')
        with pytest.raises(ValueError): w.import_workspace(None)
        if state != 'connected':
            with pytest.raises(ValueError): w.save_workspace(tmp_path / '不得生成.ramp-workspace')
        assert saved_files(w.library.root) == before and not commands
    finally:
        w.connected = False
        w.busy = False
        w.board_page.process = old_process
        monkeypatch.setattr(w.board_page.flash_page, 'running', old_running)
        w.update_buttons()


def test_cancelled_dialog_and_bad_archive_do_not_save_or_replace_edits(desktop, tmp_path, monkeypatch):
    app, make, close, warnings, commands = desktop
    w = make()
    path = tmp_path / '可取消.ramp-workspace'
    w.save_workspace(path)
    w.name.setText('取消后仍在编辑')
    before = saved_files(w.library.root)
    monkeypatch.setattr(QFileDialog, 'getSaveFileName', lambda *args: ('', ''))
    monkeypatch.setattr(QFileDialog, 'getOpenFileName', lambda *args: ('', ''))
    w.choose_save_workspace()
    w.choose_import_workspace()
    assert saved_files(w.library.root) == before
    monkeypatch.setattr(QFileDialog, 'getOpenFileName', lambda *args: (str(path), ''))
    monkeypatch.setattr(QMessageBox, 'exec', lambda *args: 0)
    w.choose_import_workspace()  # No accept button was clicked.
    assert saved_files(w.library.root) == before and w.name.text() == '取消后仍在编辑'
    bad = tmp_path / '损坏.ramp-workspace'
    bad.write_bytes(b'not a zip file')
    monkeypatch.setattr(QFileDialog, 'getOpenFileName', lambda *args: (str(bad), ''))
    w.choose_import_workspace()
    assert len(warnings) == 1 and saved_files(w.library.root) == before
    assert w.name.text() == '取消后仍在编辑' and w.isVisible() and w.thread.isRunning()
    assert not commands


def test_failed_exit_snapshot_does_not_replace_archive_or_start_import(desktop, tmp_path, monkeypatch):
    app, make, close, warnings, commands = desktop
    w = make()
    path = tmp_path / '原有全局包.ramp-workspace'
    w.save_workspace(path)
    existing = path.read_bytes()
    snapshot = read_archive(path)
    original_save = w.session.save
    def fail(*args): raise OSError('模拟设置盘写入失败')
    monkeypatch.setattr(w.session, 'save', fail)
    try:
        with pytest.raises(OSError, match='写入失败'): w.save_workspace(path)
        with pytest.raises(OSError, match='写入失败'): w.import_workspace(snapshot)
        assert path.read_bytes() == existing and w.isVisible() and w.thread.isRunning()
        assert w.centralWidget().isEnabled() and not w._exit_saved and not commands
        assert not list((w.library.root / 'archive/workspaces').glob('*.ramp-workspace'))
    finally: monkeypatch.setattr(w.session, 'save', original_save)
