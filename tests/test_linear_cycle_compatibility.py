"""Control retiming must preserve all observable signals, not only DAC codes."""
from pathlib import Path
import random
import shutil
import subprocess

import pytest


@pytest.mark.parametrize('bits',[10,12,14])
@pytest.mark.parametrize('scenario',['fixed_one','fixed_many','gated'])
def test_linear_control_retime_matches_frozen_19_clock_player(tmp_path,bits,scenario):
    if not shutil.which('iverilog') or not shutil.which('vvp'):
        pytest.skip('Icarus Verilog required')
    root=Path(__file__).resolve().parents[1]
    old=(root/'tests/fixtures/linear_engine_before_control_retime.v').read_text()
    assert 'DAC_BITS+5 clocks' in old and 'CAPTURE=2' in old
    (tmp_path/'old.v').write_text(old.replace('module linear_engine #','module old_linear_engine #',1))
    rng=random.Random(20260916+bits+len(scenario));maximum=(1<<bits)-1
    initial=maximum//3;target=maximum//5;gated=scenario=='gated'
    if gated:
        descriptors=[((initial+target)//2,32),(target,65),((initial+target)//2,33),(initial,32)]
    else:
        count=1 if scenario=='fixed_one' else 13
        descriptors=[(rng.randrange(maximum+1),32+(i%4)) for i in range(count)]
    (tmp_path/'mem.hex').write_text('\n'.join(f'{(code<<32)|n:012x}' for code,n in descriptors))
    configs=[(1,1)] if gated else [(external,repeat) for external in (0,1) for repeat in (1,3,0)]
    block=24000 if gated else 7000
    controls=[]
    for external,repeat in configs:
        for cycle in range(block):
            arm=int(cycle==2 or (cycle>1000 and cycle%601==0))
            disarm=int(cycle>1000 and cycle%701==315)
            abort=int(cycle>1000 and cycle%397==125)
            rising=int(cycle in (60,400) or (cycle>1000 and rng.randrange(29)==0))
            falling=int(cycle in (250,420) or (cycle>1000 and rng.randrange(37)==0))
            soft=int(cycle==60 or (cycle>1000 and rng.randrange(17)==0))
            external_trigger=int(cycle==60 or (cycle>1000 and rng.randrange(19)==0))
            controls.append((repeat<<8)|(external<<7)|(arm<<6)|(disarm<<5)|(abort<<4)|
                            (rising<<3)|(falling<<2)|(soft<<1)|external_trigger)
    (tmp_path/'events.hex').write_text('\n'.join(f'{v:06x}' for v in controls))
    tb=f'''`timescale 1ns/1ps
module tb;
 localparam BITS={bits};
 reg clk=0;always #2.5 clk=!clk;
 reg rst=1,arm=0,disarm=0,abort_run=0,soft_trigger=0,external_trigger=0;
 reg rising=0,falling=0,external_mode=0;
 reg[15:0] repeats=1;
 reg[23:0] events[0:{len(controls)-1}];
 reg[BITS+31:0] mem[0:{len(descriptors)-1}],d0,d1;
 wire r0,r1,p0,p1;wire[12:0] idx0,idx1,run0,run1;
 wire[1:0] s0,s1;wire[BITS-1:0] c0,c1;wire[15:0] loops0,loops1;
 wire[31:0] ignored0,ignored1;wire[2:0] phase0,phase1;
 linear_engine #(.DAC_BITS(BITS)) current(clk,rst,arm,disarm,abort_run,soft_trigger,external_trigger,
 rising,falling,1'b1,external_mode,1'b{int(gated)},
 {bits}'d{initial},{bits}'d{maximum},{bits}'d{target},repeats,14'd{len(descriptors)},14'd2,32'd65,
 r0,idx0,d0,s0,p0,c0,run0,loops0,ignored0,phase0);
 old_linear_engine #(.DAC_BITS(BITS)) frozen(clk,rst,arm,disarm,abort_run,soft_trigger,external_trigger,
 rising,falling,1'b1,external_mode,1'b{int(gated)},
 {bits}'d{initial},{bits}'d{maximum},{bits}'d{target},repeats,14'd{len(descriptors)},14'd2,32'd65,
 r1,idx1,d1,s1,p1,c1,run1,loops1,ignored1,phase1);
 always @(posedge clk)begin if(r0)d0<=mem[idx0];if(r1)d1<=mem[idx1];end
 integer i,cancelled_pushes;
 initial begin
 $readmemh("mem.hex",mem);$readmemh("events.hex",events);cancelled_pushes=0;
 repeat(3)begin @(posedge clk);#0.1;end
 @(negedge clk);rst=0;
 for(i=0;i<{len(controls)};i=i+1)begin
  {{repeats,external_mode,arm,disarm,abort_run,rising,falling,soft_trigger,external_trigger}}=events[i];
  rst=i%{block}==0;
  // On a selected later prefetch, cancel precisely when the private FIFO
  // write fires. Both old and new interfaces must clear readiness together.
  if(i>1000 && i%997<150 && current.producer==current.PUSH)begin
   abort_run=1;cancelled_pushes=cancelled_pushes+1;
  end
  // Exercise display saturation and round-counter wrap without simulating
  // millions of samples; both players receive the same reachable state.
  if(i%{block}==500 && repeats==0 && s0==2)begin
   current.completed_loops=65534;frozen.completed_loops=65534;
   current.round_loops=65534;frozen.round_loops=65534;
  end
  @(posedge clk);#0.1;
  if({{r0,idx0,s0,p0,c0,run0,loops0,ignored0,phase0}}!==
     {{r1,idx1,s1,p1,c1,run1,loops1,ignored1,phase1}})
   $fatal(1,"linear compatibility clock %0d bits={bits} scenario={scenario} code %0d/%0d state %0d/%0d phase %0d/%0d run %0d/%0d loops %0d/%0d",i,c0,c1,s0,s1,phase0,phase1,run0,run1,loops0,loops1);
  @(negedge clk);
 end
 if(cancelled_pushes==0)$fatal(1,"no PUSH cancellation exercised");
 $display("PASS frozen linear control comparison");$finish;
 end
endmodule
'''
    (tmp_path/'tb.v').write_text(tb)
    out=tmp_path/'linear_compare.vvp'
    compiled=subprocess.run(['iverilog','-g2012','-s','tb','-o',str(out),
                              str(root/'rtl/common/linear_engine.v'),str(tmp_path/'old.v'),str(tmp_path/'tb.v')],
                             capture_output=True,text=True)
    assert compiled.returncode==0,compiled.stderr
    result=subprocess.run(['vvp',str(out)],cwd=tmp_path,capture_output=True,text=True,timeout=40)
    assert result.returncode==0,result.stdout+result.stderr
    assert 'PASS frozen linear control comparison' in result.stdout
