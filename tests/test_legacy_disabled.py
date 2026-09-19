"""Disabled legacy upload commands must reject without touching waveform banks."""
from pathlib import Path
import shutil
import subprocess

import pytest


def test_disabled_legacy_begin_and_write_preserve_active_and_pending_waveforms(tmp_path):
    if not shutil.which('iverilog') or not shutil.which('vvp'):
        pytest.skip('Icarus Verilog required')
    root = Path(__file__).resolve().parents[1]
    expected = []
    for start, end in [(355, 0), (0, 355)]:
        expected.extend((2 * (start * 32 + (end - start) * k) + 32) // 64
                        for k in range(32))
    (tmp_path / 'expected.hex').write_text('\n'.join(f'{v:03x}' for v in expected))
    bench = tmp_path / 'legacy_disabled_tb.v'
    bench.write_text(r'''`timescale 1ns/1ps
module tb_legacy_disabled;
 reg clk=0;always #2.5 clk=~clk;
 reg rst=1,valid=0;reg [255:0] request=0;
 wire [239:0] response;wire response_valid;wire [9:0] code;wire [5:0] monitor;
 // Use 10 bits so valid legacy payloads would really be accepted if the
 // disabled-feature guard were lost; rejection must not depend on DAC width.
 ramp_controller #(.DAC_COUNT(1),.DAC_BITS(10),.RUN_ADDR_BITS(11),
  .CACHE_ENABLED(0),.LEGACY_ENABLED(0)) dut(
  clk,rst,request,valid,response,response_valid,1'b1,2'b0,2'b0,code,monitor);
 reg [191:0] payload,legacy_begin,legacy_write;
 reg bank_before,watch=0;
 reg [41:0] active0,active1,pending0,pending1;
 reg [9:0] expected[0:63];
 integer seq=0,errors=0,seen=0,guard;
 task tick;begin @(negedge clk);#0.1;end endtask
 task send;
  input [7:0] cmd;input [191:0] data;input [7:0] error_expected;
  begin
   tick;seq=seq+1;request={16'b0,data,seq[15:0],cmd,8'h01,16'h5aa5};valid=1;
   tick;valid=0;guard=0;
   while(!response_valid && guard<1000)begin tick;guard=guard+1;end
   if(!response_valid)$fatal(1,"command timeout %h",cmd);
   if(response[31:24]!==(cmd|8'h80) || response[47:32]!==seq[15:0])
    $fatal(1,"reply header changed");
   if(response[55:48]!==error_expected)begin
    if(cmd=='h10 || cmd=='h11)begin
     // Continue to verify bank integrity before reporting all wrong replies.
     $display("LEGACY_DISABLED_ERROR cmd=%h got=%0d expected=%0d",cmd,response[55:48],error_expected);
     errors=errors+1;
    end else $fatal(1,"command %h error %0d expected %0d",cmd,response[55:48],error_expected);
   end
   tick;
  end
 endtask
 task begin_linear;
  input integer initial_value,identifier;
  begin
   payload=0;payload[15:0]=initial_value;payload[31:16]=77;payload[47:32]=2;
   payload[63:48]=1;payload[95:64]=identifier;payload[135:128]=1;
   send('h34,payload,0);
  end
 endtask
 task run;
  input integer index,value;
  begin payload=0;payload[15:0]=index;payload[31:16]=value;payload[63:32]=32;send('h31,payload,0);end
 endtask
 task unchanged_banks;
  begin
   if(dut.active_bank!==bank_before || !dut.active_valid || !dut.active_linear ||
      dut.active_id!=11 || dut.active_initial!=100 || dut.active_runs!=2)
    $fatal(1,"legacy rejection changed active configuration");
   if(!dut.pending_valid || !dut.pending_samples || !dut.pending_linear ||
      dut.pending_id!=22 || dut.pending_initial!=355 || dut.pending_safe!=77 ||
      dut.pending_runs!=2 || dut.sample_loaded!=2)
    $fatal(1,"legacy rejection changed pending configuration");
   if(dut.sample_memory[{bank_before,11'd0}]!==active0 ||
      dut.sample_memory[{bank_before,11'd1}]!==active1 ||
      dut.sample_memory[{!bank_before,11'd0}]!==pending0 ||
      dut.sample_memory[{!bank_before,11'd1}]!==pending1)
    $fatal(1,"legacy rejection modified active or pending descriptors");
  end
 endtask
 // Playback must still be the exact pending waveform, not merely a valid table.
 always @(posedge clk)begin
  #0.2;
  if(watch && dut.run_state==2)begin
   if(seen>=64)$fatal(1,"legacy command inserted or repeated a playback clock");
   if(code!==expected[seen])$fatal(1,"pending waveform corrupted at %0d: %0d expected %0d",seen,code,expected[seen]);
   seen=seen+1;
  end
 end
 initial begin
  $readmemh("expected.hex",expected);
  legacy_begin=0;legacy_begin[15:0]=7;legacy_begin[31:16]=16;
  legacy_begin[47:32]=1;legacy_begin[63:48]=1;legacy_begin[95:64]=99;
  legacy_begin[127:112]=7;legacy_begin[175:160]=4;
  legacy_write=0;legacy_write[31:16]=31;legacy_write[63:32]=32;
  repeat(3)tick;rst=0;
  send('h10,legacy_begin,1);send('h11,legacy_write,1);
  if(dut.pending_valid || dut.active_valid)$fatal(1,"disabled commands created a configuration");
  begin_linear(100,11);run(0,800);run(1,100);send('h12,0,0);
  bank_before=dut.active_bank;
  begin_linear(355,22);run(0,0);run(1,355);
  active0=dut.sample_memory[{bank_before,11'd0}];active1=dut.sample_memory[{bank_before,11'd1}];
  pending0=dut.sample_memory[{!bank_before,11'd0}];pending1=dut.sample_memory[{!bank_before,11'd1}];
  send('h10,legacy_begin,1);unchanged_banks;
  send('h11,legacy_write,1);unchanged_banks;
  send('h12,0,0);
  if(dut.active_id!=22 || dut.active_bank===bank_before)$fatal(1,"correct pending commit lost");
  send('h20,0,0);guard=0;
  while(!monitor[2] && guard<200)begin tick;guard=guard+1;end
  if(!monitor[2])$fatal(1,"committed waveform did not prepare");
  // Unsupported takes precedence over armed/busy as well as incomplete state.
  send('h10,legacy_begin,1);send('h11,legacy_write,1);
  watch=1;send('h21,0,0);
  send('h10,legacy_begin,1);send('h11,legacy_write,1);
  guard=0;while(dut.run_state!=3 && guard<200)begin tick;guard=guard+1;end
  tick;watch=0;
  if(seen!=64 || code!=355 || dut.completed_loops!=1 || dut.active_id!=22)
   $fatal(1,"pending waveform did not finish unchanged: samples=%0d code=%0d",seen,code);
  if(errors!=0)$fatal(1,"LEGACY_ENABLED=0 returned wrong errors for %0d disabled commands",errors);
  $display("PASS disabled legacy rejection and preserved active/pending waveform");$finish;
 end
 initial begin #1000000;$fatal(1,"timeout");end
endmodule
''', encoding='ascii')
    output = tmp_path / 'legacy_disabled.vvp'
    compiled = subprocess.run(['iverilog', '-g2012', '-s', 'tb_legacy_disabled',
                               '-o', str(output), *map(str, (root / 'rtl/common').glob('*.v')),
                               str(bench)], capture_output=True, text=True)
    assert compiled.returncode == 0, compiled.stderr
    result = subprocess.run(['vvp', str(output)], cwd=tmp_path,
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'PASS disabled legacy rejection' in result.stdout
