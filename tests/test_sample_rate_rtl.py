import pytest
from test_uart_baud_rtl import test_real_200mhz_uart_frames_at_selected_baud as uart_check
from test_linear_controller import test_linear_controller_protocol_events_and_four_channels as controller_check


@pytest.mark.parametrize('hz',[100_000_000,125_000_000])
@pytest.mark.parametrize('baud',[115200,1000000])
def test_uart_with_independent_peer_at_lower_sample_clock(tmp_path,hz,baud):
    uart_check(tmp_path,baud,clock_hz=hz)


@pytest.mark.parametrize('hz',[100_000_000,125_000_000])
def test_controller_rate_info_gate_limits_and_multichannel_events(tmp_path,hz):
    controller_check(tmp_path,clock_hz=hz)
