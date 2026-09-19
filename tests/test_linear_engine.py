"""200 MHz RTL compared at every clock against independent integer arithmetic."""
from pathlib import Path
import hashlib
import math
import random
import shutil
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[1]


def samples(start, descriptors):
    result = []
    for end, ticks in descriptors:
        # All DAC values are nonnegative: this also defines negative-slope ties.
        result.extend((2 * (start * ticks + (end-start) * k) + ticks) // (2*ticks)
                      for k in range(ticks))
        start = end
    return result


def simulate(tmp_path, body, *, bits=14, descriptors=(), expected=(), extra=""):
    if not shutil.which("iverilog") or not shutil.which("vvp"):
        pytest.skip("Icarus Verilog is not installed")
    (tmp_path / 'descriptors.hex').write_text('\n'.join(f'{(e<<32)|n:012x}' for e,n in descriptors))
    (tmp_path / 'expected.hex').write_text('\n'.join(f'{v:04x}' for v in expected) or '0000')
    tb = f"""`timescale 1ns/1ps
module tb;
 localparam BITS={bits};
 reg clk=0; always #2.5 clk=!clk;
 reg rst=1,arm=0,disarm=0,abort_run=0,soft_trigger=0,external_trigger=0;
 reg rising=0,falling=0,active_valid=1,external_mode=0,gated=0;
 reg [BITS-1:0] initial_code=0,safe_code=17,target_code=0;
 reg [15:0] repeats=1;
 reg [13:0] run_count={len(descriptors)},rise_count=1;
 reg [31:0] fall_ticks=32;
 wire read_en,prepared; wire[12:0] read_index,run_index;
 reg [BITS+31:0] descriptor;
 wire[1:0] state; wire[BITS-1:0] code; wire[15:0] completed_loops;
 wire[31:0] ignored; wire[2:0] phase;
 reg[BITS+31:0] mem[0:{max(len(descriptors)-1,0)}];
 reg[BITS-1:0] expected[0:{max(len(expected)-1,0)}];
 linear_engine #(.DAC_BITS(BITS)) dut(.*);
 always @(posedge clk) if(read_en) descriptor<=mem[read_index];
 integer i,j,guard;
 task tick; begin @(posedge clk); #0.1; end endtask
 task prepare; begin
  @(negedge clk); arm=1; tick; arm=0;
  if(prepared) $fatal(1,"ARM was prepared before parameter prefetch");
  guard=0;
  while(!prepared && guard<200) begin tick; guard=guard+1; end
  if(!prepared || state!=1) $fatal(1,"ARM preparation failed");
 end endtask
 task check(input integer value); begin
  if(code!==value) $fatal(1,"clock %0t sample %0d code=%0d expected=%0d phase=%0d run=%0d",$time,i,code,value,phase,run_index);
 end endtask
 always @(posedge clk) if(!rst && dut.pop && dut.occupancy==0)
  $fatal(1,"parameter FIFO underrun");
 {extra}
 initial begin
  $readmemh("descriptors.hex",mem); $readmemh("expected.hex",expected);
  repeat(3) tick; @(negedge clk); rst=0;
  {body}
  $display("PASS linear engine"); $finish;
 end
 initial begin #10000000; $fatal(1,"test timeout"); end
endmodule
"""
    (tmp_path / 'tb.v').write_text(tb)
    subprocess.run(['iverilog','-g2012','-s','tb','-o',str(tmp_path/'sim.vvp'),
                    str(ROOT/'rtl/common/linear_engine.v'), str(ROOT/'tests/fixtures/legacy_gate_engine.v'), str(tmp_path/'tb.v')],
                   check=True,capture_output=True,text=True)
    result=subprocess.run(['vvp',str(tmp_path/'sim.vvp')],cwd=tmp_path,check=True,
                          capture_output=True,text=True,timeout=30)
    assert 'PASS linear engine' in result.stdout


@pytest.mark.parametrize('bits', [10,12,14])
def test_every_clock_signed_constant_short_segments_repeats(tmp_path,bits):
    rng=random.Random(140200+bits); maximum=(1<<bits)-1; initial=maximum//3
    descriptors=[(maximum,32),(0,32),(1,32),(0,32),(maximum//2,33),
                 (maximum//2,32),(initial,35)]
    descriptors += [(rng.randrange(maximum+1),rng.randrange(32,100)) for _ in range(40)]
    # Deliberately end away from initial: the next whole-wave round restarts I.
    oracle=samples(initial,descriptors)*3
    body=f"""
 initial_code={initial}; repeats=3; prepare;
 @(negedge clk); soft_trigger=1; tick; soft_trigger=0; check(expected[0]);
 for(i=1;i<{len(oracle)};i=i+1) begin tick; check(expected[i]); end
 tick; check(initial_code);
 if(state!=3 || completed_loops!=3) $fatal(1,"finite repeat finish mismatch");
 """
    simulate(tmp_path,body,bits=bits,descriptors=descriptors,expected=oracle)


def test_one_segment_infinite_external_rearm_and_abort(tmp_path):
    descriptors=[(0,32)]; oracle=samples(17,descriptors)*5
    body=f"""
 initial_code=17; safe_code=16383; repeats=0; external_mode=1; prepare;
 @(negedge clk); external_trigger=1; tick; external_trigger=0; check(expected[0]);
 for(i=1;i<{len(oracle)};i=i+1) begin
  @(negedge clk); external_trigger=(i==47); tick; external_trigger=0; check(expected[i]);
 end
 if(state!=2 || completed_loops!=4 || ignored!=1) $fatal(1,"infinite/busy-trigger semantics");
 @(negedge clk); abort_run=1; tick; abort_run=0; check(safe_code);
 if(state!=0 || prepared) $fatal(1,"abort must invalidate readiness");
 repeats=1; prepare;
 @(negedge clk); external_trigger=1; tick; external_trigger=0;
 for(i=1;i<32;i=i+1) tick;
 tick; check(initial_code);
 if(state!=1 || prepared || completed_loops!=1) $fatal(1,"external auto rearm mismatch");
 guard=0; while(!prepared && guard<200) begin tick; guard=guard+1; end
 if(!prepared) $fatal(1,"failed to prepare repeated external trigger");
 @(negedge clk); disarm=1; tick; disarm=0;
 if(state!=0 || prepared) $fatal(1,"disarm mismatch");
 """
    simulate(tmp_path,body,descriptors=descriptors,expected=oracle)


@pytest.mark.parametrize('ticks',[65535,65536,0x7fffffff,0xfffffffe,0xffffffff])
@pytest.mark.parametrize('start,end',[(0,16383),(16383,0)])
def test_full_32_bit_durations_prefix_no_overflow(tmp_path,ticks,start,end):
    # Long durations cannot be simulated to completion. Exercise their first
    # clocks, then inject the mathematically derived DDA state near the end.
    prefix=[(2*(start*ticks+(end-start)*k)+ticks)//(2*ticks) for k in range(96)]
    k=ticks-4
    tail=[(2*(start*ticks+(end-start)*j)+ticks)//(2*ticks) for j in range(k,ticks)]
    bias=ticks//2 if end>=start else (ticks-1)//2
    r=abs(end-start)%ticks
    error=((abs(end-start)*k+bias)%ticks)+r-ticks
    body=f"""
 initial_code={start}; prepare;
 @(negedge clk); soft_trigger=1; tick; soft_trigger=0; check(expected[0]);
 for(i=1;i<96;i=i+1) begin tick; check(expected[i]); end
 @(negedge clk); dut.code={tail[0]}; dut.remaining=4; dut.remaining_last=0;
 dut.error_acc=33'h{error & ((1<<33)-1):09x};
 tick; check({tail[1]}); tick; check({tail[2]}); tick; check({tail[3]});
 tick; check(initial_code); if(state!=3) $fatal(1,"32-bit duration tail mismatch");
 """
    simulate(tmp_path,body,descriptors=[(end,ticks)],expected=prefix)


def gaussian_descriptors(nodes):
    ticks=14000; initial=5685
    curve=lambda k: initial*(math.exp(-5*(1-k/ticks)**2)-math.exp(-5))/(1-math.exp(-5))
    # Direct formula rounding, instead of subtracting rounded magnitude.
    up=lambda k: math.floor(initial-curve(k)+.5)
    down=lambda k: math.floor(initial*(1-(math.exp(-5*(k/ticks)**2)-math.exp(-5))/(1-math.exp(-5)))+.5)
    boundaries=[round(k*ticks/nodes) for k in range(nodes+1)]
    desc=[(up(b),b-a) for a,b in zip(boundaries,boundaries[1:])]
    desc += [(down(b),b-a) for a,b in zip(boundaries,boundaries[1:])]
    exact=[up(k) for k in range(ticks)]+[down(k) for k in range(ticks)]
    return desc,exact


@pytest.mark.parametrize('nodes,limit',[(64,2),(128,1)])
def test_gaussian_all_28000_samples_normal_gate(tmp_path,nodes,limit):
    descriptors,full=gaussian_descriptors(nodes)
    oracle=samples(5685,descriptors)
    assert len(descriptors)==2*nodes and len(oracle)==28000
    assert max(abs(a-b) for a,b in zip(oracle,full))<=limit
    body="""
 initial_code=5685; target_code=0; gated=1; rise_count=%d; fall_ticks=14000; prepare;
 @(negedge clk); rising=1; tick; rising=0; check(expected[0]);
 for(i=1;i<14000;i=i+1) begin tick; check(expected[i]); end
 tick; check(target_code);
 if(phase!=2) $fatal(1,"gate did not enter HOLD at exact boundary");
 repeat(17) begin tick; check(target_code); end
 @(negedge clk); falling=1; tick; falling=0; check(expected[14000]);
 for(i=14001;i<28000;i=i+1) begin tick; check(expected[i]); end
 tick; check(initial_code);
 if(state!=1 || phase!=0 || completed_loops!=1) $fatal(1,"gate did not auto rearm");
 """ % nodes
    simulate(tmp_path,body,descriptors=descriptors,expected=oracle)


def test_actual_adaptive_gaussian_139_segments_all_28000_clocks(tmp_path):
    # Frozen output from the full-sample adaptive compiler validation in
    # build/linear_numeric_evidence. The source fixture keeps this integration
    # vector reproducible after build cleanup. Storage format is N<<14 | E;
    # simulate() repacks into the engine's {E,N} synchronous read bus.
    words=[int(v,16) for v in (ROOT/'tests/fixtures/linear_gaussian_adaptive.mem').read_text().split()]
    descriptors=[(word&16383,word>>14) for word in words]
    oracle=samples(5685,descriptors)
    _,full=gaussian_descriptors(128)
    assert len(descriptors)==139 and sum(n for _,n in descriptors[:70])==14000
    assert len(oracle)==28000 and min(n for _,n in descriptors)>=32
    assert max(abs(a-b) for a,b in zip(oracle,full))==1
    # The independent integer oracle matches every point in the actual
    # compiler's archived expected file, not merely its error summary.
    expected_hash=hashlib.sha256('\n'.join(f'{v:04x}' for v in oracle).encode()).hexdigest()
    assert expected_hash=='98bfa77ddebb7c12218d37b7180a11df8f05c3e8b819c6f64a24235b8a4fb03d'
    body="""
 initial_code=5685; target_code=0; gated=1; rise_count=70; fall_ticks=14000; prepare;
 @(negedge clk); rising=1; tick; rising=0; check(expected[0]);
 for(i=1;i<14000;i=i+1) begin tick; check(expected[i]); end
 tick; check(target_code); if(phase!=2) $fatal(1,"adaptive gate hold boundary");
 repeat(19) begin tick; check(target_code); end
 @(negedge clk); falling=1; tick; falling=0; check(expected[14000]);
 for(i=14001;i<28000;i=i+1) begin tick; check(expected[i]); end
 tick; check(initial_code);
 if(state!=1 || phase!=0 || completed_loops!=1) $fatal(1,"adaptive gate finish");
 """
    simulate(tmp_path,body,descriptors=descriptors,expected=oracle)


@pytest.mark.parametrize('start,end',[(355,0),(0,5685)])
@pytest.mark.parametrize('fall_ticks',[1,2,32,103])
def test_early_gate_fall_matches_legacy_clock_for_clock(tmp_path,start,end,fall_ticks):
    capture=samples(start,[(end,64)])[17]
    extra=f"""
 wire old_read_en,old_prepared; wire[12:0] old_read_index,old_run_index;
 wire[1:0] old_state; wire[BITS-1:0] old_code; wire[2:0] old_phase;
 wire[15:0] old_completed; wire[31:0] old_ignored;
 reg[BITS+31:0] old_descriptor;
 gate_engine #(.DAC_BITS(BITS)) old(.clk(clk),.rst(rst),.arm(arm),.disarm(disarm),
  .abort_run(abort_run),.rising(rising),.falling(falling),.active_valid(active_valid),
  .initial_code(initial_code),.safe_code(safe_code),.target_code(target_code),
  .run_count(run_count),.rise_count(rise_count),.fall_ticks(fall_ticks),
  .read_en(old_read_en),.read_index(old_read_index),.descriptor(old_descriptor),
  .state(old_state),.prepared(old_prepared),.code(old_code),.run_index(old_run_index),
  .completed_loops(old_completed),.ignored(old_ignored),.phase(old_phase));
 always @(posedge clk) if(old_read_en)
  old_descriptor<=old_read_index==0 ? (46'd{capture}<<32)|64 : (46'd{start}<<32)|{fall_ticks};
 """
    body=f"""
 initial_code={start}; target_code={end}; gated=1; fall_ticks={fall_ticks}; prepare;
 if(!old_prepared) $fatal(1,"legacy not prepared");
 @(negedge clk); rising=1; tick; rising=0;
 for(i=1;i<=17;i=i+1) tick;
 check({capture});
 @(negedge clk); falling=1; tick; falling=0;
 guard=0;
 while(state==2 && guard<200) begin
  if(code!==old_code || phase!==old_phase || state!==old_state)
   $fatal(1,"early gate mismatch new=%0d/%0d old=%0d/%0d",code,phase,old_code,old_phase);
  tick; guard=guard+1;
 end
 if(state!=1 || code!=initial_code || old_state!=1 || old_code!=initial_code)
  $fatal(1,"early return finish mismatch");
 """
    simulate(tmp_path,body,descriptors=[(end,64),(start,max(32,fall_ticks))],extra=extra)


def test_fall_exact_enter_boundary_uses_table_without_setup(tmp_path):
    desc=[(0,32),(355,32)]; oracle=samples(355,desc)
    body="""
 initial_code=355; target_code=0; gated=1; prepare;
 @(negedge clk); rising=1; tick; rising=0; check(expected[0]);
 for(i=1;i<32;i=i+1) begin tick; check(expected[i]); end
 @(negedge clk); falling=1; tick; falling=0; check(expected[32]);
 if(phase!=3) $fatal(1,"boundary fall entered early-return setup");
 for(i=33;i<64;i=i+1) begin tick; check(expected[i]); end
 tick; check(initial_code); if(state!=1) $fatal(1,"boundary fall finish");
 """
    simulate(tmp_path,body,descriptors=desc,expected=oracle)


def test_2048_minimum_duration_descriptors_sustain_prefetch(tmp_path):
    rng=random.Random(2048200)
    descriptors=[(rng.randrange(16384),32) for _ in range(2048)]
    oracle=samples(8192,descriptors)
    body=f"""
 initial_code=8192; prepare;
 @(negedge clk); soft_trigger=1; tick; soft_trigger=0; check(expected[0]);
 for(i=1;i<{len(oracle)};i=i+1) begin tick; check(expected[i]); end
 tick; check(initial_code);
 if(state!=3 || completed_loops!=1) $fatal(1,"2048 descriptor finish mismatch");
 """
    simulate(tmp_path,body,descriptors=descriptors,expected=oracle)


@pytest.mark.parametrize('abort_phase',[1,2,3,4,5,6])
def test_abort_from_each_gate_phase_and_clean_rearm(tmp_path,abort_phase):
    body=f"""
 initial_code=355; target_code=0; gated=1; fall_ticks=64; prepare;
 @(negedge clk); rising=1; tick; rising=0;
 if({abort_phase}==2 || {abort_phase}==3) begin
  guard=0; while(phase!=2 && guard<100) begin tick; guard=guard+1; end
 end
 if({abort_phase}>=3) begin
  @(negedge clk); falling=1; tick; falling=0;
 end
 guard=0;
 while(phase!={abort_phase} && guard<100) begin tick; guard=guard+1; end
 if(phase!={abort_phase}) $fatal(1,"did not reach requested abort phase");
 @(negedge clk); abort_run=1; tick; abort_run=0; check(safe_code);
 if(state!=0 || prepared) $fatal(1,"gate abort not idle");
 prepare; check(initial_code);
 @(negedge clk); rising=1; tick; rising=0; check(initial_code);
 if(state!=2 || phase!=1 || run_index!=0) $fatal(1,"stale data after gate abort");
 """
    simulate(tmp_path,body,descriptors=[(0,64),(355,64)])


@pytest.mark.parametrize('cancel',['abort_run','disarm'])
def test_cancel_coincident_private_fifo_write_requires_fresh_prefetch(tmp_path,cancel):
    # Cancellation deliberately coincides with a PUSH. Its private RAM write
    # may occur, but the following ARM must consume only the fresh waveform.
    oracle=samples(3000,[(4000,32),(3000,32)])
    body=f"""
 initial_code=5685; safe_code=16383;
 @(negedge clk); arm=1; tick; arm=0;
 guard=0; while(dut.producer!=dut.PUSH && guard<100) begin tick; guard=guard+1; end
 if(dut.producer!=dut.PUSH) $fatal(1,"did not reach private FIFO write");
 @(negedge clk); {cancel}=1; tick; {cancel}=0;
 if(prepared || state!=0 || dut.occupancy!=0) $fatal(1,"cancel left consumable stale FIFO");
 initial_code=3000;mem[0]=(46'd4000<<32)|32;mem[1]=(46'd3000<<32)|32;
 prepare;
 @(negedge clk); soft_trigger=1; tick; soft_trigger=0; check(expected[0]);
 for(i=1;i<64;i=i+1) begin tick; check(expected[i]); end
 tick; check(initial_code);
 if(state!=3 || completed_loops!=1) $fatal(1,"fresh waveform finish after cancelled write");
 """
    simulate(tmp_path,body,descriptors=[(0,32),(5685,32)],expected=oracle)
