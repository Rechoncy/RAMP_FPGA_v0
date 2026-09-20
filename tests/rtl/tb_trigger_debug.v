`timescale 1ns/1ps
module tb_trigger_debug;
 reg clk=0;always #2.5 clk=~clk;
 reg rst=1,configure=0,action=0,heartbeat=0,release_debug=0;
 reg [7:0] requested_mask=0;reg [2:0] index=7;reg [1:0] operation=0;
 reg [31:0] high_ticks=1,period_ticks=2,count=1;
 wire [7:0] mask,levels,rising,falling;wire running,expired;wire [2:0] active;wire [31:0] emitted;
 trigger_debug #(.COUNT(8),.CORE_HZ(100)) dut(clk,rst,configure,action,heartbeat,release_debug,1'b0,
  requested_mask,index,operation,high_ticks,period_ticks,count,mask,levels,rising,falling,running,expired,active,emitted);
 task tick;begin @(negedge clk);#0.1;end endtask
 integer i,n,h,p,start_cycle,last_rise,last_fall,cycle=0;
 always @(negedge clk) cycle=cycle+1;
 initial begin
  repeat(3) tick;rst=0;configure=1;requested_mask=128;tick;configure=0;
  if(rising || falling || mask!=128) $fatal(1,"configuration generated edge");
  for(h=1;h<=5;h=h+1) begin
   high_ticks=h;period_ticks=h+3;count=3;operation=3;action=1;tick;action=0;
   last_rise=cycle;n=1;start_cycle=cycle;
   if(rising!=128) $fatal(1,"missing first edge");
   while(running) begin
    tick;
    if(falling) begin if(cycle-last_rise!=h) $fatal(1,"high width");last_fall=cycle;end
    if(rising) begin if(cycle-last_rise!=h+3 || cycle-last_fall!=3) $fatal(1,"period");last_rise=cycle;n=n+1;end
   end
   if(n!=3 || levels) $fatal(1,"pulse count");
  end
  operation=1;action=1;tick;action=0;if(!rising[7]) $fatal(1,"set high");
  tick;action=1;tick;action=0;if(rising) $fatal(1,"duplicate high");
  repeat(310) tick;if(!expired || levels || mask!=128) $fatal(1,"watchdog");
  release_debug=1;tick;release_debug=0;if(mask || rising || falling) $fatal(1,"release edge");
  $display("PASS trigger debug exact pulse widths, finite train, static levels, watchdog, 8 inputs");$finish;
 end
endmodule
