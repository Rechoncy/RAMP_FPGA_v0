"""An edge during interpolation prefetch must be counted, never advance a step."""
from pathlib import Path
import shutil
import subprocess

import pytest


def test_mixed_one_clock_hold_to_linear_prefetch_trigger_accounting(tmp_path):
    if not shutil.which('iverilog') or not shutil.which('vvp'):
        pytest.skip('Icarus Verilog required')
    root=Path(__file__).resolve().parents[1]
    # Reuse the integrated wire-protocol helper, with a private generated
    # bench/output so this test can run concurrently with the base regression.
    text=(root/'tests/rtl/tb_linear_controller.v').read_text()
    marker='  $display("PASS linear controller:'
    assert text.count(marker)==1
    addition="""
  // A one-clock legacy step immediately followed by an interpolated step.
  // Deliberately fire the NEXT step's correct source during quotient prefetch.
  send('h40,0,0);begin_program(1);run(0,444,1);run(1,0,32);run(2,1000,32);
  step(0,0,0,0,1,0,0);step(1,0,1,1,2,1,0);step(2,1,0,1,2,1,0);
  send('h12,0,0);send('h20,0,0);wait_step(0);edge_r(1);
  wait(dut.channels[0].engine.program_step==1 &&
       dut.channels[0].engine.program_phase==6 &&
       !dut.channels[0].engine.program_arm &&
       dut.channels[0].engine.linear_state==1 &&
       !dut.channels[0].engine.linear_prepared);
  edge_r(2);
  if(dut.channels[0].engine.program_step!=1 ||
     dut.channels[0].engine.program_ignored!=1 ||
     dut.channels[0].engine.program_phase!=6)
   $fatal(1,"prepare-time trigger advanced silently or was not counted");
  wait_step(1);repeat(7)tick;
  if(dut.channels[0].engine.program_state!=1 || codes[13:0]!=1000)
   $fatal(1,"prepare-time event was queued unexpectedly");
  // Exactly 64 clocks once ready: transition from I into the negative slope.
  edge_r(2);
  if(codes[13:0]!=1000 || dut.channels[0].engine.linear_state!=2)
   $fatal(1,"ready trigger did not start linear k=0");
  for(i=1;i<64;i=i+1)begin
   tick;
   if(i<32)j=(2*(1000*32-1000*i)+32)/64;
   else j=(2*(1000*(i-32))+32)/64;
   if(codes[13:0]!=j)$fatal(1,"mixed minimum-segment sample %0d = %0d expected %0d",i,codes[13:0],j);
  end
  wait_step(2);
  if(dut.channels[0].engine.program_ignored!=1)
   $fatal(1,"unexpected ignored count after ready playback");
  edge_r(1);repeat(40)tick;edge_f(1);wait_step(0);
  if(dut.channels[0].engine.program_loops!=1)
   $fatal(1,"mixed short-step table did not wrap");
  send('h22,0,0);
"""
    text=text.replace(marker,addition+marker)
    bench=tmp_path/'boundary_tb.v';bench.write_text(text)
    out=tmp_path/'boundary.vvp'
    compile_result=subprocess.run(['iverilog','-g2012','-s','tb_linear_controller','-o',str(out),
                                    *map(str,(root/'rtl/common').glob('*.v')),str(bench)],
                                   capture_output=True,text=True)
    assert compile_result.returncode==0,compile_result.stderr
    result=subprocess.run(['vvp',str(out)],capture_output=True,text=True,timeout=60)
    assert result.returncode==0,result.stdout+result.stderr
    assert 'PASS linear controller:' in result.stdout
