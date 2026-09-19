`timescale 1ns/1ps
module tb_auto_rearm;
 reg clk=0;always #2.5 clk=~clk;
 reg rst=1,arm=0,disarm=0,abort_run=0,trig=0;
 reg [41:0] mem[0:2],descriptor;
 wire read_en,prepared;wire [12:0] address,index;wire [1:0] state;
 wire [9:0] code;wire [15:0] loops;wire [31:0] ignored;
 sample_engine dut(clk,rst,arm,disarm,abort_run,1'b0,trig,1'b1,1'b1,
  10'd512,10'd777,16'd2,14'd3,read_en,address,descriptor,state,prepared,code,index,loops,ignored);
 always @(posedge clk) if(read_en) descriptor<=mem[address];
 task tick;begin @(negedge clk);end endtask
 integer event_no,loop_no,i;
 initial begin
  mem[0]={10'd100,32'd1};mem[1]={10'd200,32'd1};mem[2]={10'd300,32'd1};
  repeat(4) tick;rst=0;arm=1;tick;arm=0;
  for(event_no=1;event_no<=4;event_no=event_no+1) begin
   wait(prepared);tick;trig=1;tick;trig=0;
   for(loop_no=0;loop_no<2;loop_no=loop_no+1) for(i=0;i<3;i=i+1) begin
    if(state!==2 || code!==mem[i][41:32]) $fatal(1,"wrong sample after automatic rearm");
    // A pulse while busy, including the final completion edge, is ignored.
    trig=1;tick;trig=0;
   end
   if(state!==1 || code!==512 || loops!==event_no*2) $fatal(1,"wrong cumulative count or return");
   if(prepared) $fatal(1,"stale FIFO accepted as prepared");
  end
  if(ignored!=24) $fatal(1,"busy edge count");
  wait(prepared);tick;disarm=1;tick;disarm=0;trig=1;tick;trig=0;
  if(state!==0 || code!==512) $fatal(1,"disarm auto-started");
  arm=1;tick;arm=0;wait(prepared);tick;trig=1;tick;trig=0;abort_run=1;tick;abort_run=0;
  repeat(15) tick;if(state!==0 || code!==777) $fatal(1,"abort auto-rearmed");
  $display("PASS fixed external: consecutive events, one-clock runs, repeats, ignored busy edges, disarm, abort");$finish;
 end
 initial begin #100000;$fatal(1,"timeout");end
endmodule
