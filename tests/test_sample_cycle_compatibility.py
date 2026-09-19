"""Retimed one-clock RLE playback must match the immutable published engine."""
from pathlib import Path
import random
import shutil
import subprocess

import pytest


@pytest.mark.parametrize('bits',[10,12,14])
@pytest.mark.parametrize('count',[1,2,2048])
def test_sample_all_outputs_match_published_player_every_clock(tmp_path,bits,count):
    if not shutil.which('iverilog') or not shutil.which('vvp'):
        pytest.skip('Icarus Verilog required')
    root=Path(__file__).resolve().parents[1]
    old=(root/'tests/fixtures/legacy_sample_engine.v').read_text()
    (tmp_path/'old.v').write_text(old.replace('module sample_engine #','module old_sample_engine #',1))
    rng=random.Random(20260915+bits+count);maximum=(1<<bits)-1
    descriptors=[(rng.randrange(maximum+1),1) for _ in range(count)]
    (tmp_path/'mem.hex').write_text('\n'.join(f'{(code<<32)|n:012x}' for code,n in descriptors))
    # Every finite mode gets enough undisturbed clocks for three whole tables.
    # Then randomized controls exercise abort/disarm and repeated ARM at any
    # descriptor boundary. External modes exercise automatic rearm + refill.
    block=count*3+3000
    controls=[]
    for external in [0,1]:
        for repeat in [1,3,0]:
            for cycle in range(block):
                arm=int(cycle==2 or (cycle>count*3+20 and cycle%401==0))
                disarm=int(cycle>count*3+20 and cycle%701==315)
                abort=int(cycle>count*3+20 and cycle%397==125)
                soft=int(cycle==20 or (cycle>20 and rng.randrange(13)==0))
                trigger=int(cycle==20 or (cycle>20 and rng.randrange(11)==0))
                controls.append((repeat<<6)|(external<<5)|(arm<<4)|(disarm<<3)|(abort<<2)|(soft<<1)|trigger)
    (tmp_path/'events.hex').write_text('\n'.join(f'{v:06x}' for v in controls))
    tb=f'''`timescale 1ns/1ps
module tb;
 localparam BITS={bits};
 reg clk=0;always #2.5 clk=!clk;
 reg rst=1,arm=0,disarm=0,abort_run=0,soft_trigger=0,external_trigger=0,external_mode=0;
 reg[15:0] repeats=1;
 reg[21:0] events[0:{len(controls)-1}];
 reg[BITS+31:0] mem[0:{count-1}],d0,d1;
 wire r0,r1,p0,p1;wire[12:0] idx0,idx1,run0,run1;
 wire[1:0] s0,s1;wire[BITS-1:0] c0,c1;wire[15:0] loops0,loops1;
 wire[31:0] ignored0,ignored1;
 sample_engine #(.DAC_BITS(BITS)) current(clk,rst,arm,disarm,abort_run,soft_trigger,external_trigger,
 1'b1,external_mode,{bits}'d{maximum//3},{bits}'d{maximum},repeats,14'd{count},
 r0,idx0,d0,s0,p0,c0,run0,loops0,ignored0);
 old_sample_engine #(.DAC_BITS(BITS)) published(clk,rst,arm,disarm,abort_run,soft_trigger,external_trigger,
 1'b1,external_mode,{bits}'d{maximum//3},{bits}'d{maximum},repeats,14'd{count},
 r1,idx1,d1,s1,p1,c1,run1,loops1,ignored1);
 always @(posedge clk)begin if(r0)d0<=mem[idx0];if(r1)d1<=mem[idx1];end
 integer i;
 initial begin
 $readmemh("mem.hex",mem);$readmemh("events.hex",events);
 repeat(3)begin @(posedge clk);#0.1;end
 @(negedge clk);rst=0;
 for(i=0;i<{len(controls)};i=i+1)begin
  {{repeats,external_mode,arm,disarm,abort_run,soft_trigger,external_trigger}}=events[i];
  // Change metadata only during the deliberate idle boundary between cases.
  if(i%{block}==0)begin rst=1;end else rst=0;
  @(posedge clk);#0.1;
  if({{r0,idx0,s0,p0,c0,run0,loops0,ignored0}}!=={{r1,idx1,s1,p1,c1,run1,loops1,ignored1}})
   $fatal(1,"sample compatibility clock %0d bits={bits} count={count} code %0d/%0d state %0d/%0d index %0d/%0d",i,c0,c1,s0,s1,run0,run1);
  @(negedge clk);
 end
 $display("PASS immutable sample comparison");$finish;
 end
endmodule
'''
    (tmp_path/'tb.v').write_text(tb)
    out=tmp_path/'sample_compare.vvp'
    compiled=subprocess.run(['iverilog','-g2012','-s','tb','-o',str(out),
                              str(root/'rtl/common/sample_engine.v'),str(tmp_path/'old.v'),str(tmp_path/'tb.v')],
                             capture_output=True,text=True)
    assert compiled.returncode==0,compiled.stderr
    result=subprocess.run(['vvp',str(out)],cwd=tmp_path,capture_output=True,text=True,timeout=40)
    assert result.returncode==0,result.stdout+result.stderr
    assert 'PASS immutable sample comparison' in result.stdout
