"""Reference clock configuration survives editing, migration and FPGA export."""
import json
import os
from dataclasses import replace
from pathlib import Path

import pytest

from host.board import Board, clock_ports, generate_xdc, ports_for_counts, validate
from host.boot import generate_boot
from host.model import Profile

ROOT = Path(__file__).resolve().parents[1]


def test_legacy_single_clock_migration_preserves_constraints():
    old = json.loads((ROOT / 'boards/xc7a35t_four_dac.json').read_text(encoding='utf-8'))
    migrated = Board.from_dict(old)
    assert migrated.schema == 3 and migrated.clock_input == 'single_ended'
    assert generate_xdc(migrated) == generate_xdc(Board(**{**old, 'schema': 3}))
    old['io_overrides'] = {'sys_clk': 'LVCMOS18'}
    migrated = Board.from_dict(old)
    assert 'PACKAGE_PIN R4 IOSTANDARD LVCMOS18' in generate_xdc(migrated)
    assert not validate(migrated)


@pytest.mark.parametrize('mhz', [50, 100, 200])
@pytest.mark.parametrize('kind', ['single_ended', 'differential'])
@pytest.mark.parametrize('boot', [False, True])
def test_clock_constraints_and_boot_snapshot(tmp_path, mhz, kind, boot):
    # Pin placeholders test export syntax only; physical pairs require Vivado.
    board = Board(ref_mhz=mhz, clock_input=kind,
                  pins={'sys_clk': 'R4', 'sys_clk_p': 'A1', 'sys_clk_n': 'A2'},
                  boot_profiles={'0': Profile(mode='ramp').to_dict()} if boot else {})
    text = generate_xdc(board)
    assert text.count('create_clock -name sys_clk') == 1
    assert f'-period {1000/mhz:.6f} [get_ports {{{clock_ports(kind)[0]}}}]' in text
    prefix = 'core/' * (int(boot) + int(kind == 'differential'))
    assert f'-source [get_pins {prefix}u_clock/u_ip/inst/mmcm_adv_inst/CLKOUT0]' in text
    if kind == 'differential':
        assert '[get_ports {sys_clk}]' not in text
        for pin in ['sys_clk_p', 'sys_clk_n']:
            line = next(line for line in text.splitlines() if 'PACKAGE_PIN' in line and pin in line)
            assert 'IOSTANDARD LVDS_25' in line
        assert 'DIFF_TERM FALSE' in text
        assert 'DIFF_TERM TRUE' in generate_xdc(replace(board, clock_diff_term=True))
    else:
        assert 'sys_clk_p' not in text and 'DIFF_TERM' not in text
    if boot:
        directory = tmp_path / 'boot'
        generate_boot(board, directory)
        wrapper = (directory / 'ramp_boot_top.v').read_text()
        module = 'ramp_top_diff' if kind == 'differential' else 'ramp_top'
        assert f'{module} #(' in wrapper
        for pin in clock_ports(kind):
            assert f'.{pin}({pin})' in wrapper
        manifest = json.loads((directory / 'boot_manifest.json').read_text())
        assert manifest['ref_mhz'] == mhz and manifest['clock_input'] == kind
        assert manifest['channels'][0]['profile']['initial'] == board.boot_profiles['0']['initial']


def test_invalid_clocks_fail_before_build():
    for changes in [dict(ref_mhz=0), dict(ref_mhz=float('nan')), dict(ref_mhz=float('inf')),
                    dict(clock_input='auto'), dict(clock_io_standard='LVDS_25'),
                    dict(clock_diff_term=True), dict(clock_diff_term='false'),
                    dict(clock_input='differential', clock_io_standard='LVCMOS33'),
                    dict(clock_input='differential', io_overrides={'sys_clk_n': 'LVCMOS18'})]:
        with pytest.raises(ValueError):
            generate_xdc(Board(**changes))
    b = Board(clock_input='differential')
    assert 'sys_clk_p' in '\n'.join(validate(b, complete=True))
    assert 'sys_clk_n' in '\n'.join(validate(b, complete=True))
    b.pins.update(sys_clk_p='A1', sys_clk_n='A1')
    assert '同时分配' in '\n'.join(validate(b))
    # Inactive single-ended pin must not conflict with the active P input.
    b.pins.update(sys_clk='A1', sys_clk_n='A2')
    assert not validate(b)


def test_clock_editor_switch_save_import_and_pin_ownership(tmp_path, monkeypatch):
    os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
    from PySide6.QtWidgets import QApplication, QFileDialog, QMessageBox
    from PySide6.QtCore import Qt
    from host.board_page import BoardPage
    from host.storage import Library
    app = QApplication.instance() or QApplication([])
    failures = []
    monkeypatch.setattr(QMessageBox, 'warning', lambda *args: failures.append(args[2]))
    page = BoardPage(Library(tmp_path))
    page.resize(1050, 760)
    page.show()
    app.processEvents()

    def row(port):
        return next(i for i in range(page.table.rowCount()) if page.table.item(i, 0).text() == port)

    try:
        for mhz, button in zip([50, 100, 200], page.clock_presets):
            button.click()
            assert page.collect().ref_mhz == mhz
        page.clock_io.setCurrentIndex(page.clock_io.findData('LVCMOS18'))
        page.clock_input.setCurrentIndex(page.clock_input.findData('differential'))
        assert page.table.item(row('sys_clk_p'), 1).text() == ''
        assert page.table.item(row('sys_clk_n'), 1).text() == ''
        page.table.item(row('sys_clk_p'), 1).setText('A1')
        page.table.item(row('sys_clk_n'), 1).setText('A2')
        page.clock_diff_term.setChecked(True)
        assert not page.table.item(row('sys_clk_p'), 2).flags() & Qt.ItemIsEditable
        page.port_filter.setCurrentIndex(page.port_filter.findData('clock'))
        visible = [page.table.item(i, 0).text() for i in range(page.table.rowCount()) if not page.table.isRowHidden(i)]
        assert visible == ['sys_clk_p', 'sys_clk_n']
        page.clock_input.setCurrentIndex(page.clock_input.findData('single_ended'))
        assert page.table.item(row('sys_clk'), 1).text() == 'R4'
        assert page.collect().clock_io_standard == 'LVCMOS18'
        assert not page.collect().clock_diff_term
        page.clock_input.setCurrentIndex(page.clock_input.findData('differential'))
        assert page.table.item(row('sys_clk_p'), 1).text() == 'A1'
        assert page.collect().clock_io_standard == 'LVDS_25'
        assert page.save()
        saved = json.loads((tmp_path / 'board.json').read_text(encoding='utf-8'))
        reloaded = Board.from_dict(saved)
        assert reloaded.clock_diff_term and reloaded.ref_mhz == 200
        assert reloaded.pins['sys_clk'] == 'R4'
        assert not any(p in reloaded.io_overrides for p in ['sys_clk', 'sys_clk_p', 'sys_clk_n'])
        page.clock_input.setCurrentIndex(page.clock_input.findData('single_ended'))
        monkeypatch.setattr(QFileDialog, 'getOpenFileName', lambda *a, **k: (str(tmp_path / 'board.json'), ''))
        page.import_board()
        assert page.collect().to_dict() == reloaded.to_dict()
        assert page.table.rowCount() == len(ports_for_counts(2, 2, 10, 'differential'))
        assert not failures, failures
    finally:
        page.close()
