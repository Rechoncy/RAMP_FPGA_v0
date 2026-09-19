"""Integrated protocol, bank validation and independent channel/event checks."""
from pathlib import Path
import shutil
import subprocess
import pytest


def test_linear_controller_protocol_events_and_four_channels(tmp_path, clock_hz=200_000_000):
    if not shutil.which('iverilog') or not shutil.which('vvp'):
        pytest.skip('Icarus Verilog required')
    root = Path(__file__).resolve().parents[1]
    sources = list((root / 'rtl/common').glob('*.v'))
    output = tmp_path / 'linear_controller.vvp'
    compiled = subprocess.run(['iverilog', '-g2012', '-s', 'tb_linear_controller',
                               f'-Ptb_linear_controller.CORE_HZ={clock_hz}', '-o', str(output), *map(str, sources),
                               str(root / 'tests/rtl/tb_linear_controller.v')],
                              capture_output=True, text=True)
    assert compiled.returncode == 0, compiled.stderr
    result = subprocess.run(['vvp', str(output)], capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'PASS linear controller:' in result.stdout


@pytest.mark.parametrize('reset_cycles', [1, 6])
@pytest.mark.parametrize('linear,gated', [(False, False), (False, True), (True, False), (True, True)])
def test_linear_boot_configuration_arms_without_host_and_keeps_capacity(tmp_path, reset_cycles, linear, gated):
    """BOOT_CONFIG bit 7 uses the same 46-bit, two-bank table as UART uploads."""
    if not shutil.which('iverilog') or not shutil.which('vvp'):
        pytest.skip('Icarus Verilog required')
    root = Path(__file__).resolve().parents[1]
    memory = tmp_path / 'linear_boot.mem'
    words = [(0 << 32) | 32, (5685 << 32) | 32] + [0] * (4096 - 2)
    memory.write_text('\n'.join(f'{word:012x}' for word in words), encoding='ascii')
    config = (1 | 4 | (2 if gated else 0) | (128 if linear else 0)) | (5685 << 16) | (700 << 32) | (1 << 48)
    config |= (7 << 64) | (2 << 96) | (1 << 112) | (32 << 128)
    filename = int.from_bytes(str(memory).replace('\\', '/').encode('ascii'), 'big')
    bench = tmp_path / 'boot_tb.v'
    bench.write_text(f'''`timescale 1ns/1ps
module tb_linear_boot;
 reg clk=0;always #2.5 clk=~clk;reg rst=1;reg [1:0] rise=0,fall=0;
 wire [55:0] codes;wire [239:0] reply;wire valid;
 parallel_controller #(.DAC_BITS(14),.BOOT_CONFIGS(2048'h{config:x}),.BOOT_FILES(16384'h{filename:x})) dut(
 clk,rst,256'b0,1'b0,reply,valid,1'b1,rise,fall,codes);
 task tick;begin @(negedge clk);#0.1;end endtask
 initial begin
 repeat({reset_cycles})tick;rst=0;repeat(100)tick;
 if(dut.channels[0].engine.use_linear!={int(linear)} || !dut.monitor[0][2] || codes[13:0]!=5685)$fatal(1,"boot ARM");
 rise=1;tick;rise=0;
 // The first sample distinguishes interpolation (I) from held-code (0).
 if(codes[13:0]!={5685 if linear else 0})$fatal(1,"boot playback mode first sample");
 repeat(40)tick;
 if({int(gated)})begin
  if(codes[13:0]!=0 || dut.channels[0].engine.selected_gate_phase!=2)$fatal(1,"boot gate HOLD");
  fall=1;tick;fall=0;
 end
 repeat(100)tick;
 if(codes[13:0]!=5685 || !dut.monitor[0][2])$fatal(1,"boot automatic rearm");
 if(codes[55:14]!={{3{{14'd8192}}}})$fatal(1,"other DACs disturbed");
 $display("PASS linear boot");$finish;
 end
 initial begin #100000;$fatal(1,"timeout");end
endmodule
''', encoding='ascii')
    output = tmp_path / 'boot.vvp'
    compiled = subprocess.run(['iverilog', '-g2012', '-s', 'tb_linear_boot', '-o', str(output),
                               *map(str, (root / 'rtl/common').glob('*.v')), str(bench)], capture_output=True, text=True)
    assert compiled.returncode == 0, compiled.stderr
    result = subprocess.run(['vvp', str(output)], capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'PASS linear boot' in result.stdout
