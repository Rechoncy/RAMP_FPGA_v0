`timescale 1ns/1ps
module tb_debug;
 parameter BITS=14,COUNT=19;
 reg clk=0; always #2.5 clk=~clk;
 reg rst=1,valid=0;
 reg [7:0] trigger_rise=0,trigger_fall=0;
 reg [255:0] request=0;
 wire [239:0] response;
 wire response_valid;
 wire [8*BITS-1:0] codes;
 reg [255:0] vectors[0:COUNT-1];
 reg [111:0] expected[0:COUNT-1];
 integer i,j;
 ramp_controller #(.DAC_COUNT(8),.TRIGGER_COUNT(8),.DAC_BITS(BITS)) dut(
  clk,rst,request,valid,response,response_valid,1'b1,trigger_rise,trigger_fall,codes,);
 initial begin
  $readmemh("build/debug_vectors.hex",vectors);
  $readmemh("build/debug_expected.hex",expected);
  repeat(5) @(negedge clk); rst=0;
  for(i=0;i<COUNT;i=i+1) begin
   @(negedge clk); request=vectors[i]; valid=1;
   @(negedge clk); valid=0;
   wait(response_valid); #0.1;
   if(response[55:48]!==0 || response[47:32]!==request[47:32]) $fatal(1,"bad debug response %d",i);
   for(j=0;j<100;j=j+1) begin
    @(negedge clk); trigger_rise=~trigger_rise; trigger_fall=~trigger_fall;
    if(codes!==expected[i][8*BITS-1:0]) $fatal(1,"static DAC route/value mismatch frame=%d",i);
    if(dut.sample_state!==0) $fatal(1,"static output must never arm or run");
   end
  end
  $display("PASS static: Python frames, all DAC routes, trigger immunity, idle hold and abort, bits=%d",BITS);
  $finish;
 end
 initial begin #1000000; $fatal(1,"timeout"); end
endmodule
