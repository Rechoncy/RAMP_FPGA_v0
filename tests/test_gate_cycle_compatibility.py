"""Compare the retimed legacy gate against the immutable published RTL."""
from pathlib import Path
import random
import shutil
import subprocess

import pytest


@pytest.mark.parametrize('bits',[10,12,14])
def test_legacy_gate_all_outputs_match_published_player_every_clock(tmp_path,bits):
    if not shutil.which('iverilog') or not shutil.which('vvp'):
        pytest.skip('Icarus Verilog required')
    root=Path(__file__).resolve().parents[1]
    old=(root/'tests/fixtures/legacy_gate_engine.v').read_text()
    (tmp_path/'old.v').write_text(old.replace('module gate_engine #','module old_gate_engine #',1))
    rng=random.Random(20260915+bits);maximum=(1<<bits)-1
    # Short descriptors exercise every-cycle transitions; periodic ARM/abort
    # and random edges exercise busy and early-fall behavior in every phase.
    descriptors=[(rng.randrange(maximum+1),n) for n in [1,2,1,35,1,17]]
    (tmp_path/'mem.hex').write_text('\n'.join(f'{(code<<32)|n:012x}' for code,n in descriptors))
    controls=[]
    for cycle in range(24000):
        arm=int(cycle%250==0);disarm=int(cycle%701==315);abort=int(cycle%397==125)
        rise=int(rng.randrange(13)==0);fall=int(rng.randrange(19)==0)
        controls.append((arm<<4)|(disarm<<3)|(abort<<2)|(rise<<1)|fall)
    (tmp_path/'events.hex').write_text('\n'.join(f'{v:02x}' for v in controls))
    tb=f'''`timescale 1ns/1ps
module tb;
 localparam BITS={bits};
 reg clk=0;always #2.5 clk=!clk;
 reg rst=1,arm=0,disarm=0,abort_run=0,rising=0,falling=0;
 reg[4:0] events[0:23999];
 reg[BITS+31:0] mem[0:5],d0,d1;
 wire r0,r1,p0,p1;wire[12:0] idx0,idx1,run0,run1;
 wire[1:0] s0,s1;wire[BITS-1:0] c0,c1;wire[15:0] loops0,loops1;
 wire[31:0] ignored0,ignored1;wire[2:0] phase0,phase1;
 gate_engine #(.DAC_BITS(BITS)) current(clk,rst,arm,disarm,abort_run,rising,falling,1'b1,
 BITS'd{maximum//3},BITS'd{maximum},BITS'd{maximum//7},14'd6,14'd3,32'd53,
 r0,idx0,d0,s0,p0,c0,run0,loops0,ignored0,phase0);
 old_gate_engine #(.DAC_BITS(BITS)) published(clk,rst,arm,disarm,abort_run,rising,falling,1'b1,
 BITS'd{maximum//3},BITS'd{maximum},BITS'd{maximum//7},14'd6,14'd3,32'd53,
 r1,idx1,d1,s1,p1,c1,run1,loops1,ignored1,phase1);
 always @(posedge clk)begin if(r0)d0<=mem[idx0];if(r1)d1<=mem[idx1];end
 integer i;
 initial begin
 $readmemh("mem.hex",mem);$readmemh("events.hex",events);
 repeat(3)begin @(posedge clk);#0.1;end
 @(negedge clk);rst=0;
 for(i=0;i<24000;i=i+1)begin
  {{arm,disarm,abort_run,rising,falling}}=events[i];
  @(posedge clk);#0.1;
  if({{r0,idx0,s0,p0,c0,run0,loops0,ignored0,phase0}}!==
     {{r1,idx1,s1,p1,c1,run1,loops1,ignored1,phase1}})
   $fatal(1,"legacy compatibility clock %0d bits={bits} code %0d/%0d phase %0d/%0d",i,c0,c1,phase0,phase1);
  @(negedge clk);
 end
 $display("PASS immutable legacy gate comparison");$finish;
 end
endmodule
'''
    # Width constants use sized integer literals, whose width must be numeric.
    tb=tb.replace("BITS'd",f"{bits}'d")
    (tmp_path/'tb.v').write_text(tb)
    out=tmp_path/'gate_compare.vvp'
    compiled=subprocess.run(['iverilog','-g2012','-s','tb','-o',str(out),
                              str(root/'rtl/common/gate_engine.v'),str(tmp_path/'old.v'),str(tmp_path/'tb.v')],
                             capture_output=True,text=True)
    assert compiled.returncode==0,compiled.stderr
    result=subprocess.run(['vvp',str(out)],cwd=tmp_path,capture_output=True,text=True,timeout=30)
    assert result.returncode==0,result.stdout+result.stderr
    assert 'PASS immutable legacy gate comparison' in result.stdout
