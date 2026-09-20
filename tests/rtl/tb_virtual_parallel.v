`timescale 1ns/1ps
module tb_virtual_parallel;
 parameter BITS=10;
 localparam MID=1<<(BITS-1);
 reg clk=0;always #2.5 clk=~clk;
 reg source=0;reg rst=1,valid=0;reg [255:0] request=0;wire [239:0] response;wire response_valid;
 reg [1:0] rising=0,falling=0;wire [4*BITS-1:0] codes;
 reg [191:0] p;integer seq=0,i,j;reg [255:0] saved;
 parallel_controller #(.SOURCE_TAGGED(1),.CORE_HZ(1000),.DAC_BITS(BITS)) dut(clk,rst,request,valid,response,response_valid,1'b1,rising,falling,codes);
 task tick;begin @(negedge clk);#0.1;end endtask
 task send;input [7:0] cmd;input [191:0] data;input [7:0] err;
 begin
  tick;seq=seq+1;request={16'b0,data,seq[15:0],cmd,8'h01,16'h5aa5};request[240]=source;valid=1;
  tick;valid=0;wait(response_valid);#0.1;
  if(response[55:48]!==err || response[31:24] !== (cmd|8'h80)) $fatal(1,"cmd %h error %d expected %d",cmd,response[55:48],err);
  tick;
 end endtask
 task load;input integer ch;input integer gate_mode;
 begin
  send('h40,ch,0);p=0;p[15:0]=MID+ch;p[31:16]=MID+ch;p[47:32]=3;p[63:48]=1;p[95:64]=100+ch;
  if(gate_mode) begin p[103:96]=ch;p[111:104]=ch%2;p[127:112]=MID-10-ch;p[159:128]=40;send('h32,p,0);end
  else begin p[111:104]=ch;p[135:128]=1;send('h30,p,0);end
  for(j=0;j<3;j=j+1) begin p=0;p[15:0]=j;p[31:16]=MID-10-ch;p[63:32]=100+j*10;send('h31,p,0);end
  send('h12,0,0);
 end endtask
 initial begin
  repeat(6) tick;rst=0;send(1,0,0);
  if(response[175:168]!=4 || response[167:152]!=2048 || response[191:184]!=239) $fatal(1,"caps");
  // Reject all-channel ARM without partial effects if any channel lacks a plan.
  load(0,0);send('h41,16'h0f20,4);
  for(i=0;i<4;i=i+1) if(dut.monitor[i][1:0]!=0) $fatal(1,"partial arm");
  for(i=1;i<4;i=i+1) load(i,0);
  for(i=0;i<4;i=i+1) if(codes[i*BITS+:BITS]!=MID+i) $fatal(1,"upload disturbed another channel");
  send('h41,16'h0f20,0);send('h41,16'h0f21,0);saved=request;
  for(i=0;i<4;i=i+1) if(codes[i*BITS+:BITS]!=MID-10-i) $fatal(1,"parallel output");
  // Retry must return the cached group result without starting again.
  tick;request=saved;valid=1;tick;valid=0;wait(response_valid);#0.1;
  if(response[55:48]!=0) $fatal(1,"retry group");
  repeat(400) tick;
  for(i=0;i<4;i=i+1) if(codes[i*BITS+:BITS]!=MID+i) $fatal(1,"return initial");
  for(i=0;i<4;i=i+1) begin send('h40,i,0);send(2,0,0);if(response[191:184]!=i || response[103:88]!=1) $fatal(1,"per channel status");end
  // Two independent gate inputs; channels sharing a source start on the same cycle.
  for(i=0;i<4;i=i+1) load(i,1);
  send('h60,3,0);send('h41,16'h0f20,0);send('h60,1,2);tick;rising=1;tick;rising=0;repeat(20) tick;
  if(dut.monitor[0][1:0]!=1) $fatal(1,"physical edge not masked");
  send('h61,16'h0100,0);repeat(120) tick;
  if(dut.monitor[0][1:0]!=2 || dut.monitor[2][1:0]!=2 || dut.monitor[1][1:0]!=1 || dut.monitor[3][1:0]!=1) $fatal(1,"trigger routing");
  send('h61,16'h0101,0);repeat(120) tick;
  for(i=0;i<4;i=i+1) if(codes[i*BITS+:BITS]!=MID-10-i) $fatal(1,"gate hold");
  send('h61,0,0);repeat(300) tick;
  if(codes[0+:BITS]!=MID || codes[2*BITS+:BITS]!=MID+2 || codes[BITS+:BITS]!=MID-11) $fatal(1,"independent return");
  send('h63,0,0);
  for(i=0;i<4;i=i+1) if(codes[i*BITS+:BITS]!=MID+i) $fatal(1,"group abort");
  send('h40,4,3);send('h41,16'h1020,3);
  send('h40,0,0);p=0;p[15:0]=MID;p[31:16]=MID;p[47:32]=2049;p[63:48]=1;send('h30,p,3);
  send('h60,1,0);source=1;send('h61,16'h0100,2);source=0;p=0;p[15:8]=2;p[47:16]=100;send('h61,p,0);saved=request;
  tick;request=saved;valid=1;tick;valid=0;wait(response_valid);tick;
  send('h62,0,0);if(response[119:88]!=1) $fatal(1,"retry duplicated pulse");
  repeat(120) tick;
  send('h60,1,0);p=0;p[15:8]=3;p[47:16]=50;p[79:48]=100;p[111:80]=3;send('h61,p,0);
  repeat(350) tick;send('h62,0,0);if(response[119:88]!=3 || response[72]) $fatal(1,"periodic count");
  send('h41,16'h0f20,0);send('h61,16'h0100,0);
  wait(dut.debug_expired);tick;seq=seq+1;request={16'b0,192'b0,seq[15:0],8'h62,8'h01,16'h5aa5};valid=1;
  tick;valid=0;wait(response_valid);tick;
  if(response[31:24]!=8'he2 || response[55:48]!=0) $fatal(1,"coincident timeout request lost");
  for(i=0;i<4;i=i+1) if(dut.monitor[i][1:0]!=0 || codes[i*BITS+:BITS]!=MID+i) $fatal(1,"watchdog failed to abort all channels");
  send('h62,0,0);if(response[63:56]!=0 || !response[73]) $fatal(1,"watchdog failed to restore physical routing");
  $display("PASS virtual parallel: isolation, atomic group rejection, group retry, simultaneous play, routed gate hold/return, capacity");$finish;
 end
 // Synchronous group commands must move all fixed engines in the same cycle.
 always @(negedge clk) if(!rst && dut.channels[0].engine.soft_trigger) begin
  if(!dut.channels[1].engine.soft_trigger || !dut.channels[2].engine.soft_trigger || !dut.channels[3].engine.soft_trigger) $fatal(1,"group trigger skew");
 end
 initial begin #500000; $fatal(1,"timeout");end
endmodule
