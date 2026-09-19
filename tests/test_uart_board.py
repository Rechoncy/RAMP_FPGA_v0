"""Static firmware UART rate survives board editing, export and boot builds."""
import io
import json
import os
from dataclasses import replace
from types import SimpleNamespace

import pytest

from host.board import Board, generate_xdc, validate
from host.boot import generate_boot
from host.model import Profile
from host.uart_settings import BAUD_RATES, DEFAULT_BAUD
from host.utilization import board_fingerprint, write_build_manifest


def test_old_board_configuration_defaults_to_existing_firmware_rate():
    for schema in (1, 2, 3):
        board = Board.from_dict({'schema': schema})
        assert board.uart_baud == DEFAULT_BAUD == 115200
        assert board.schema == 3
        assert Board.from_dict(board.to_dict()) == board


@pytest.mark.parametrize('baud', BAUD_RATES)
def test_supported_baud_rates_roundtrip_and_keep_dac_clock(baud):
    board = Board(uart_baud=baud)
    assert not validate(board)
    assert Board.from_dict(json.loads(json.dumps(board.to_dict()))).uart_baud == baud
    assert generate_xdc(board) == generate_xdc(Board())
    assert board.core_mhz == 200


@pytest.mark.parametrize('baud', [True, None, '1000000', 1000000.0, 0, -1, 12345, 1000001, 2000000])
def test_invalid_firmware_rates_rejected_before_build(baud):
    with pytest.raises(ValueError, match='UART 波特率'):
        generate_xdc(Board(uart_baud=baud))


@pytest.mark.parametrize('kind', ['single_ended', 'differential'])
def test_boot_wrapper_and_manifest_keep_uart_baud(tmp_path, kind):
    board = Board(uart_baud=1000000, clock_input=kind, boot_profiles={
        '0': Profile(mode='ramp', rise_ms=.001, hold_ms=.001, fall_ms=.001).to_dict()})
    generate_boot(board, tmp_path / 'boot')
    wrapper = (tmp_path / 'boot/ramp_boot_top.v').read_text()
    assert '.UART_BAUD(1000000)' in wrapper
    assert ('ramp_top_diff #(' if kind == 'differential' else 'ramp_top #(') in wrapper
    assert json.loads((tmp_path / 'boot/boot_manifest.json').read_text(encoding='utf-8'))['uart_baud'] == 1000000


@pytest.mark.parametrize('kind', ['single_ended', 'differential'])
def test_build_worker_appends_rate_after_existing_tcl_arguments(tmp_path, monkeypatch, kind):
    from host import build_worker
    script = tmp_path / 'source/scripts/vivado/build.tcl'
    script.parent.mkdir(parents=True)
    script.write_text('# mocked build entry')
    vivado = tmp_path / 'vivado.bat'
    vivado.write_text('rem mocked executable')
    board_path = tmp_path / 'board.json'
    board = Board(vivado=str(vivado), uart_baud=1000000, clock_input=kind,
                  pins={'sys_clk': 'R4', 'sys_clk_p': 'A1', 'sys_clk_n': 'A2'})
    board_path.write_text(json.dumps(board.to_dict()))
    commands = []

    def start(command, **kwargs):
        commands.append(command)
        return SimpleNamespace(stdout=io.BytesIO(b'mocked Vivado output\n'), wait=lambda: 0)

    monkeypatch.setattr(build_worker, 'build_script', lambda *a: script)
    monkeypatch.setattr(build_worker, 'prepare_external_runtime', lambda: None)
    monkeypatch.setattr(build_worker.subprocess, 'Popen', start)
    args = SimpleNamespace(board=str(board_path), output=str(tmp_path / 'build'), mode='generate')
    assert build_worker.run_build(args, board, {}) == 0
    command = commands[0]
    arguments = command[command.index('-tclargs') + 1:]
    assert len(arguments) == 18
    assert arguments[16:18] == ['-','200']
    assert arguments[12] == kind
    assert arguments[13:15] == (['R4', '-'] if kind == 'single_ended' else ['A1', 'A2'])
    assert arguments[15] == '1000000'


def test_uart_configuration_invalidates_resource_identity(tmp_path):
    normal = Board()
    fast = replace(normal, uart_baud=1000000)
    assert board_fingerprint(normal) != board_fingerprint(fast)
    old = normal.to_dict()
    old.pop('uart_baud')
    assert board_fingerprint(old) == board_fingerprint(normal)
    manifest = write_build_manifest(tmp_path / 'build', fast, 'generate', 'success', tmp_path)
    assert manifest['board_context']['uart_baud'] == 1000000
    assert manifest['board_fingerprint'] == board_fingerprint(fast)


def test_board_editor_saves_imports_and_restores_uart_without_actions(tmp_path, monkeypatch):
    os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
    from PySide6.QtWidgets import QApplication, QFileDialog, QMessageBox
    from host.board_page import BoardPage
    from host.storage import Library
    app = QApplication.instance() or QApplication([])
    page = BoardPage(Library(tmp_path))
    failures = []
    monkeypatch.setattr(QMessageBox, 'warning', lambda *args: failures.append(args[2]))
    try:
        assert page.uart_baud.currentData() == DEFAULT_BAUD
        assert [page.uart_baud.itemData(i) for i in range(page.uart_baud.count())] == list(BAUD_RATES)
        page.uart_baud.setCurrentIndex(page.uart_baud.findData(1000000))
        snapshot = json.loads(json.dumps(page.capture_session()))
        assert snapshot['fields']['uart_baud']['data'] == 1000000
        assert page.save()
        saved = json.loads((tmp_path / 'board.json').read_text(encoding='utf-8'))
        assert saved['uart_baud'] == 1000000
        page.uart_baud.setCurrentIndex(page.uart_baud.findData(DEFAULT_BAUD))
        assert page.restore_session(snapshot)
        assert page.collect().uart_baud == 1000000
        page.uart_baud.setCurrentIndex(page.uart_baud.findData(9600))
        monkeypatch.setattr(QFileDialog, 'getOpenFileName', lambda *a, **k: (str(tmp_path / 'board.json'), ''))
        page.import_board()
        assert page.uart_baud.currentData() == 1000000
        saved.pop('uart_baud')
        (tmp_path / 'board.json').write_text(json.dumps(saved), encoding='utf-8')
        page.import_board()
        assert page.uart_baud.currentData() == DEFAULT_BAUD
        assert page.process is None and page.flash_page.process is None
        assert not failures
    finally:
        page.close()
        app.processEvents()
