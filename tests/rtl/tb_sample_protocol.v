`timescale 1ns/1ps
module tb_sample_protocol;
 reg clk=0; always #2.5 clk=~clk;
 reg rst=1,valid=0,ext0=0,ext1=0;
 reg [255:0] request=0;
 wire [239:0] response;
 wire response_valid;
 wire [9:0] dac0,dac1;
 reg [191:0] payload;
 integer seq=0,monitor=0,seen=0,channel=0;
 reg [9:0] expected;
 ramp_controller dut(clk,rst,request,valid,response,response_valid,1'b1,{ext1,ext0},2'b0,{dac1,dac0},);
 task command; input [7:0] cmd; input [191:0] data; input [7:0] error;
  begin
   @(negedge clk); seq=seq+1; request=0; request[15:0]=16'h5aa5;
   request[23:16]=1; request[31:24]=cmd; request[47:32]=seq; request[239:48]=data; valid=1;
   @(negedge clk); valid=0; wait(response_valid); #0.1;
   if(response[55:48]!==error) $fatal(1,"command %h error %d expected %d",cmd,response[55:48],error);
   @(negedge clk);
  end
 endtask
 task begin_table; input [7:0] ch; input [7:0] external_mode; input [15:0] count;
  begin
   payload=0; payload[15:0]=512; payload[31:16]=512; payload[47:32]=count;
   payload[63:48]=2; payload[95:64]=32'h12345678; payload[103:96]=external_mode; payload[111:104]=ch;
   command(8'h30,payload,0);
  end
 endtask
 task load_run; input [15:0] index; input [15:0] code; input [31:0] length;
  begin payload=0; payload[15:0]=index; payload[31:16]=code; payload[63:32]=length; command(8'h31,payload,0); end
 endtask
 always @(negedge clk) if(monitor && dut.sample_state==2) begin
  case(seen%4) 0:expected=10; 1,2:expected=900; 3:expected=333; endcase
  if((channel==0 ? dac0:dac1)!==expected || (channel==0 ? dac1:dac0)!==512)
   $fatal(1,"channel routing/sample mismatch ch=%d tick=%d",channel,seen);
  seen=seen+1;
 end
 initial begin
  repeat(5) @(negedge clk); rst=0;
  if(dac0!==512 || dac1!==512) $fatal(1,"reset must be near zero");
  command(8'h01,0,0);
  if(response[167:152]!==8192 || response[175:168]!==2) $fatal(1,"capability mismatch");
  for(channel=0;channel<2;channel=channel+1) begin
   begin_table(channel,channel,3);
   load_run(0,10,1); command(8'h12,0,4); // incomplete must not switch banks
   load_run(1,900,2); load_run(2,333,1); command(8'h12,0,0);
   command(8'h20,0,0); command(8'h30,0,2); // immutable while armed
   monitor=1; seen=0;
   if(channel==0) command(8'h21,0,0);
   else begin
    command(8'h21,0,2);
    @(negedge clk); ext0=1; @(negedge clk); ext0=0;
    repeat(4) @(negedge clk);
    if(dut.sample_state!==1) $fatal(1,"wrong DAC trigger started waveform");
    @(negedge clk); ext1=1; @(negedge clk); ext1=0;
   end
   wait(dut.sample_state==(channel==0 ? 3:1) && dut.sample_loops==2); @(negedge clk); monitor=0;
   if(seen!=8 || dac0!==512 || dac1!==512) $fatal(1,"duration/repeat/end mismatch %d",seen);
   command(8'h02,0,0);
   if(response[191:184]!==channel || response[215:208]!==1) $fatal(1,"status routing mismatch");
   command(8'h23,0,0);
  end
  begin_table(1,0,1); load_run(0,0,1000); command(8'h12,0,0); command(8'h20,0,0); command(8'h21,0,0);
  if(dac1!==0 || dac0!==512) $fatal(1,"full positive voltage code lost");
  command(8'h22,0,0); if(dac0!==512 || dac1!==512) $fatal(1,"abort must return both channels near zero");
  // Legacy upload arithmetic: maximum quotient and a denominator > 10 bits.
  payload=0; payload[31:16]=16; payload[47:32]=2; payload[63:48]=1; payload[175:160]=1;
  command(8'h10,payload,0);
  payload=0; payload[31:16]=1023; payload[63:32]=1; command(8'h11,payload,0);
  if(dut.memory[{!dut.active_bank,6'd0}]!=={10'd1023,10'd1023,32'd0,32'd1}) $fatal(1,"max quotient mismatch");
  payload=0; payload[15:0]=1; payload[63:32]=32'hffffffff; command(8'h11,payload,0);
  if(dut.memory[{!dut.active_bank,6'd1}]!=={10'd0,10'd0,32'd1023,32'hffffffff}) $fatal(1,"large denominator mismatch");
  $display("PASS sample protocol: capabilities, double banks, partial commit, both DAC routes, selected trigger, 5 ns playback, abort"); $finish;
 end
 initial begin #100000; $fatal(1,"timeout"); end
endmodule
