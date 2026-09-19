`timescale 1ns/1ps
module tb_sequence;
 reg clk=0; always #2.5 clk=~clk;
 reg rst=1,arm=0,disarm=0,abort_run=0,soft_trigger=0,external_trigger=0;
 reg valid=1,external_mode=0;
 reg [9:0] initial_code=100,safe_code=20;
 reg [15:0] divider=16,repeats=2;
 reg [6:0] count=2;
 wire [5:0] read_index,index; reg [83:0] desc;
 wire [1:0] state; wire prepared; wire [9:0] code;
 wire [15:0] loops; wire [31:0] ignored;
 reg [83:0] memory[0:63];
 integer cycles=0,start_cycle,done_cycle,changes=0; reg [9:0] prev;
 sequence_engine dut(clk,rst,arm,disarm,abort_run,soft_trigger,external_trigger,
 valid,external_mode,initial_code,safe_code,divider,repeats,count,read_index,desc,state,prepared,code,index,loops,ignored);
 always @(posedge clk) begin desc<=memory[read_index]; cycles<=cycles+1; end
 initial begin
  // 100 -> 113 in 7 updates; 113 -> 100 in 5; two identical loops.
  memory[0]={10'd113,10'd1,32'd6,32'd7};
  memory[1]={10'd100,10'd2,32'd3,32'd5};
  repeat(5) @(negedge clk); rst=0;
  @(negedge clk); arm=1; @(negedge clk); arm=0;
  wait(prepared); @(negedge clk); soft_trigger=1; @(negedge clk); soft_trigger=0;
  wait(state==2); start_cycle=cycles; prev=code;
  while(state==2) begin
   @(negedge clk);
   if(code<100 || code>113) $fatal(1,"code out of interval: %d",code);
   if(code!=prev) changes=changes+1;
   prev=code;
  end
  done_cycle=cycles;
  if(done_cycle-start_cycle!=384) $fatal(1,"duration %d expected 384",done_cycle-start_cycle);
  if(code!=100 || loops!=2 || state!=3) $fatal(1,"wrong completion");
  // Abort from armed uses safe code and prevents pending startup.
  @(negedge clk); arm=1; @(negedge clk); arm=0;
  @(negedge clk); abort_run=1; @(negedge clk); abort_run=0;
  repeat(10) @(negedge clk);
  if(state!=0 || code!=20 || prepared) $fatal(1,"abort failed");
  // Abort exactly before a tick, then re-arm: the old tick flag must not leak.
  @(negedge clk); arm=1; @(negedge clk); arm=0;
  wait(prepared); @(negedge clk); soft_trigger=1; @(negedge clk); soft_trigger=0;
  wait(dut.time_left==1); @(negedge clk); abort_run=1; @(negedge clk); abort_run=0;
  repeat(4) @(negedge clk);
  arm=1; @(negedge clk); arm=0;
  wait(prepared); @(negedge clk); soft_trigger=1; @(negedge clk); soft_trigger=0;
  repeat(15) begin @(negedge clk); if(code!=100) $fatal(1,"early output after re-arm"); end
  @(negedge clk); if(code!=101) $fatal(1,"wrong first update after re-arm");
  $display("PASS sequence: nondivisible ramps, repeated boundaries, 384 cycles, abort"); $finish;
 end
 initial begin #100000; $fatal(1,"timeout"); end
endmodule
