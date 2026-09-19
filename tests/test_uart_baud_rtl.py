"""UART wire validation with independent ideal-baud stimulus and Python CRCs."""
import shutil
import subprocess
from pathlib import Path

import pytest

from host.protocol import encode

ROOT = Path(__file__).resolve().parents[1]
BAUD_RATES = (9600, 19200, 38400, 57600, 115200, 230400, 460800, 921600, 1000000)


@pytest.mark.parametrize('baud', BAUD_RATES)
def test_real_200mhz_uart_frames_at_selected_baud(tmp_path, baud, clock_hz=200_000_000):
    if not shutil.which('iverilog') or not shutil.which('vvp'):
        pytest.skip('Icarus Verilog is not installed')
    # Different data and sequence bytes exercise bit order and frame boundaries.
    requests = [encode(0x01, 0x5AA5, bytes(range(24))),
                encode(0x02, 0xFF00, bytes([0xA5, 0x5A, 0x00, 0xFF] * 6))]
    responses = [encode(0x81, 0x5AA5, bytes(range(23, -1, -1))),
                 encode(0x82, 0xFF00, bytes([0xFF, 0x00, 0x5A, 0xA5] * 6))]
    for name, frames in [('requests', requests), ('responses', responses)]:
        (tmp_path / f'{name}.hex').write_text(
            '\n'.join(f'{int.from_bytes(frame, "little"):064x}' for frame in frames), encoding='ascii')
    output = tmp_path / 'uart_baud.vvp'
    sources = ['rtl/common/uart.v', 'rtl/common/uart_autobaud.v', 'rtl/common/packet_link.v', 'tests/rtl/tb_uart_baud.v']
    subprocess.run(['iverilog', '-g2012', '-s', 'tb_uart_baud', f'-Ptb_uart_baud.BAUD={baud}', f'-Ptb_uart_baud.CORE_HZ={clock_hz}',
                    '-o', str(output), *[str(ROOT / source) for source in sources]],
                   check=True, capture_output=True, text=True)
    result = subprocess.run(['vvp', str(output)], cwd=tmp_path, check=True,
                            capture_output=True, text=True, timeout=90)
    assert f'PASS baud={baud}:' in result.stdout
