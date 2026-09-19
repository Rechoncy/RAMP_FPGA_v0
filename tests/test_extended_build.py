from dataclasses import replace
from pathlib import Path
import shutil
import subprocess

import pytest

from host.board import Board
from host.extended_build import generate_top, stage_platform
from host.hardware_interfaces import apply_davinci_interfaces

ROOT=Path(__file__).resolve().parents[1]


def usb_board(**kwargs):
    return apply_davinci_interfaces(Board(dac_count=4, **kwargs))


def test_uart_only_does_not_create_optional_source_folder(tmp_path):
    assert stage_platform(Board(),tmp_path/'project') is None
    assert not list(tmp_path.iterdir())


def test_usb_firmware_retains_uart_and_routes_responses_to_their_source(tmp_path):
    folder=stage_platform(usb_board(uart_baud=1000000),tmp_path/'project')
    assert {p.name for p in folder.iterdir()}=={'ramp_extended_top.v'}
    text=(folder/'ramp_extended_top.v').read_text(encoding='utf-8')
    assert 'packet_link #(.BAUD(1000000),.CLK_HZ(200000000)) u_uart' in text
    assert 'usb_packet_link u_usb' in text and 'dual_packet_router u_router' in text
    assert '.SOURCE_TAGGED(1)' in text and '.USB_FIFO_ENABLED(1)' in text
    assert '.request_ready(usb_request_ready)' in text
    assert "assign uart_tx=1'b1" not in text
    assert 'ddr3' not in text and 'memory_ready' not in text and 'EXTERNAL_MEMORY' not in text


@pytest.mark.parametrize('channels,bits',[(1,13),(2,12),(3,11),(4,11),(5,10),(8,10)])
def test_usb_preserves_existing_bram_per_channel_capacity(channels,bits):
    b=apply_davinci_interfaces(Board(dac_count=channels))
    assert f'.RUN_ADDR_BITS({bits})' in generate_top(b)


def test_differential_wrapper_keeps_both_links_and_clock_hierarchy():
    text=generate_top(replace(usb_board(),clock_input='differential'))
    assert 'input sys_clk_p,sys_clk_n,' in text
    assert 'ramp_extended_core #(.BOOT_CONFIGS(BOOT_CONFIGS),.BOOT_FILES(BOOT_FILES)) core(' in text
    assert '.usb_clk(usb_clk)' in text and '.uart_rx(uart_rx)' in text and '.uart_tx(uart_tx)' in text
    assert 'IBUFDS u_reference_buffer' in text


@pytest.mark.parametrize('clock_input',['single_ended','differential'])
def test_usb_platform_preserves_bram_boot_profiles(tmp_path,clock_input):
    from host.boot import generate_boot
    from host.model import Profile
    profile=Profile(mode='ramp',initial=512,amplitude=508,safe=513,
                    rise_ms=.00004,hold_ms=.00004,fall_ms=.00004)
    b=replace(usb_board(clock_input=clock_input),boot_profiles={'0':profile.to_dict()})
    staged=stage_platform(b,tmp_path/'project')
    boot=tmp_path/'boot'
    manifest=generate_boot(b,boot)
    text=(staged/'ramp_extended_top.v').read_text(encoding='utf-8')
    wrapper=(boot/'ramp_boot_top.v').read_text(encoding='ascii')
    assert '.BOOT_CONFIGS(BOOT_CONFIGS),.BOOT_FILES(BOOT_FILES)' in text
    assert 'ramp_extended_top #(' in wrapper and 'inputusb_clk,inout' in wrapper.replace(' ','')
    assert '.usb_data(usb_data)' in wrapper and '.uart_rx(uart_rx)' in wrapper
    assert 'DAC_COUNT' not in wrapper and manifest[0]['profile']['initial']==512
    assert (boot/'boot_dac0.mem').is_file()
    if not shutil.which('iverilog'):pytest.skip('Icarus Verilog is not installed')
    stubs=tmp_path/'xilinx.v'
    stubs.write_text('''module clk_core_ip(input clk_in1,reset,output clk_out1,locked);
 assign clk_out1=clk_in1;assign locked=!reset;endmodule
 module IBUFDS(input I,IB,output O);assign O=I;endmodule
 module ODDR #(parameter DDR_CLK_EDGE="SAME_EDGE")(input C,CE,D1,D2,R,S,output Q);
 assign Q=C;endmodule
''',encoding='ascii')
    hierarchy='dut.core.core.u_controller' if clock_input=='differential' else 'dut.core.u_controller'
    clock_ports='.sys_clk_p(clk),.sys_clk_n(~clk)' if clock_input=='differential' else '.sys_clk(clk)'
    bench=tmp_path/'tb.v'
    bench.write_text(f'''`timescale 1ns/1ps
 module tb_usb_boot;
 reg clk=0;always #2.5 clk=~clk;reg usbclk=0;always #8.333 usbclk=~usbclk;
 wire [39:0] data;wire [7:0] usb_data;
 ramp_boot_top dut({clock_ports},.trigger_in(2'b00),.uart_rx(1'b1),.dac_data(data),
 .usb_clk(usbclk),.usb_data(usb_data),.usb_rxf_n(1'b1),.usb_txe_n(1'b1));
 initial begin repeat(100) @(negedge clk);
 if(!{hierarchy}.monitor[0][3] || {hierarchy}.monitor[0][1:0]!=1 || data[9:0]!=512)
 $fatal(1,"USB boot profile not armed or initial code lost");
 $display("PASS USB + UART boot profile arms without host");$finish;end
 endmodule
''',encoding='ascii')
    result=subprocess.run(['iverilog','-g2012','-s','tb_usb_boot','-o',str(tmp_path/'sim'),str(bench),
        str(boot/'ramp_boot_top.v'),str(staged/'ramp_extended_top.v'),str(stubs),
        str(ROOT/'rtl/platform/xilinx_7series/clock_reset_adapter.v'),
        *map(str,(ROOT/'rtl/common').glob('*.v'))],capture_output=True,text=True,timeout=30)
    assert result.returncode==0,result.stderr
    result=subprocess.run(['vvp',str(tmp_path/'sim')],cwd=boot,capture_output=True,text=True,timeout=20)
    assert result.returncode==0 and 'PASS' in result.stdout,result.stdout+result.stderr


def test_staging_does_not_overwrite_previous_outputs(tmp_path):
    folder=stage_platform(usb_board(),tmp_path/'project')
    (folder/'ramp_extended_top.v').write_text('previous build',encoding='utf-8')
    with pytest.raises(FileExistsError):stage_platform(usb_board(),tmp_path/'project')
    assert (folder/'ramp_extended_top.v').read_text(encoding='utf-8')=='previous build'


@pytest.mark.parametrize('clock_input',['single_ended','differential'])
def test_generated_dual_transport_top_elaborates_with_actual_controller(tmp_path,clock_input):
    if not shutil.which('iverilog'):pytest.skip('Icarus Verilog is not installed')
    source=tmp_path/'generated.v'
    source.write_text(generate_top(usb_board(clock_input=clock_input)),encoding='utf-8')
    stubs=tmp_path/'xilinx_stubs.v'
    stubs.write_text('''module clk_core_ip(input clk_in1,reset,output clk_out1,locked);
 assign clk_out1=clk_in1;assign locked=!reset;endmodule
 module IBUFDS(input I,IB,output O);assign O=I;endmodule
 module ODDR #(parameter DDR_CLK_EDGE="SAME_EDGE")(input C,CE,D1,D2,R,S,output Q);
 assign Q=C;endmodule
''',encoding='utf-8')
    result=subprocess.run(['iverilog','-g2012','-tnull','-s','ramp_extended_top',str(source),str(stubs),
        str(ROOT/'rtl/platform/xilinx_7series/clock_reset_adapter.v'),
        *map(str,(ROOT/'rtl/common').glob('*.v'))],capture_output=True,text=True,timeout=30)
    assert result.returncode==0,result.stderr
    assert 'not a port' not in result.stderr and 'parameter' not in result.stderr.lower()
