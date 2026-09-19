"""CLI UART arguments and reports are tested with no physical port access."""
import importlib.util
import json
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(params=['verify_connected_device', 'verify_parallel_device'])
def script(request, monkeypatch):
    path = ROOT / 'scripts' / (request.param + '.py')
    spec = importlib.util.spec_from_file_location(request.param, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    opened = []

    def no_hardware(port, baudrate):
        opened.append((port, baudrate))
        raise RuntimeError('TEST: physical serial access is disabled')

    monkeypatch.setattr(module, 'SerialTransport', no_hardware)
    return module, opened


@pytest.mark.parametrize('argument,expected', [(None, 115200), ('1000000', 1000000)])
def test_cli_default_and_one_megabit_reach_transport_and_report(script, tmp_path, monkeypatch, argument, expected):
    module, opened = script
    report = tmp_path / 'report.json'
    argv = [module.__name__, '--port', 'TEST-NO-HARDWARE', '--report', str(report)]
    if argument is not None:
        argv += ['--baudrate', argument]
    monkeypatch.setattr(sys, 'argv', argv)
    assert module.main() == 1  # The stub intentionally stops before device access.
    assert opened == [('TEST-NO-HARDWARE', expected)]
    result = json.loads(report.read_text(encoding='utf-8'))
    assert result['baudrate'] == expected and not result['passed']
    assert 'physical serial access is disabled' in result['error']


def test_cli_invalid_rate_is_rejected_before_transport(script, tmp_path, monkeypatch):
    module, opened = script
    report = tmp_path / 'report.json'
    monkeypatch.setattr(sys, 'argv', [module.__name__, '--port', 'TEST-NO-HARDWARE',
                                   '--report', str(report), '--baudrate', '2000000'])
    with pytest.raises(SystemExit) as error:
        module.main()
    assert error.value.code == 2
    assert not opened and not report.exists()


def test_cli_help_lists_maximum_without_opening_transport(script, monkeypatch, capsys):
    module, opened = script
    monkeypatch.setattr(sys, 'argv', [module.__name__, '--help'])
    with pytest.raises(SystemExit) as error:
        module.main()
    assert error.value.code == 0
    help_text = capsys.readouterr().out
    assert '--baudrate' in help_text and '1000000' in help_text
    assert not opened
