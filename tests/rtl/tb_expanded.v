`timescale 1ns/1ps
module tb_expanded;
 parameter DACS=8,TRIGS=4,BITS=10;
 localparam MID=(1 << (BITS-1)), MAX=(1 << BITS)-1;
 reg clk=0; always #2.5 clk=~clk;
 reg rst=1,valid=0;
 reg [TRIGS-1:0] rising=0,falling=0;
 reg [255:0] request=0;
 wire [239:0] response;
 wire response_valid;
 wire [DACS*BITS-1:0] codes;
 reg [191:0] payload;
 integer seq=0,ch=0,src=0,seen=0,j,monitor=0;
 reg edge_fall;
 ramp_controller #(.DAC_COUNT(DACS),.TRIGGER_COUNT(TRIGS),.DAC_BITS(BITS)) dut(clk,rst,request,valid,response,response_valid,1'b1,rising,falling,codes,);
 task command; input [7:0] cmd; input [191:0] data; input [7:0] error;
  begin
   @(negedge clk); seq=seq+1; request=0; request[15:0]=16'h5aa5;
   request[23:16]=1; request[31:24]=cmd; request[47:32]=seq; request[239:48]=data; valid=1;
   @(negedge clk); valid=0; wait(response_valid); #0.1;
   if(response[55:48]!==error) $fatal(1,"command=%h got=%d expected=%d",cmd,response[55:48],error);
   @(negedge clk);
  end
 endtask
 task pulse; input integer index; input is_fall;
  begin
   @(negedge clk); if(is_fall) falling[index]=1; else rising[index]=1;
   @(negedge clk); rising=0; falling=0;
  end
 endtask
 always @(negedge clk) if(monitor && dut.sample_state==2) begin
  for(j=0;j<DACS;j=j+1)
   if(codes[j*BITS+:BITS] !== (j==ch ? (seen%3==0 ? 0:MAX):MID)) $fatal(1,"wrong expanded DAC code ch=%d sample=%d",j,seen);
  seen=seen+1;
 end
 initial begin
  repeat(5) @(negedge clk); rst=0;
  for(j=0;j<DACS;j=j+1) if(codes[j*BITS+:BITS]!==MID) $fatal(1,"wrong reset midpoint");
  command(1,0,0);
  if(response[127:120]!==BITS || response[87:56]!==200000000) $fatal(1,"wrong width/clock capability");
  if(response[175:168]!==DACS || response[183:176]!==TRIGS || response[191:184]!==235) $fatal(1,"wrong channel capability");
  for(ch=0;ch<DACS;ch=ch+1) begin
   src=(ch+1)%TRIGS; edge_fall=ch%2;
   payload=0; payload[15:0]=MID+ch; payload[31:16]=MAX-ch; payload[47:32]=2; payload[63:48]=2;
   payload[95:64]=12345+ch; payload[103:96]=1; payload[111:104]=ch;
   payload[119:112]=src; payload[127:120]=edge_fall; payload[135:128]=1;
   command(8'h30,payload,0);
   payload=0; payload[31:16]=0; payload[63:32]=1; command(8'h31,payload,0);
   payload=0; payload[15:0]=1; payload[31:16]=MAX; payload[63:32]=2; command(8'h31,payload,0);
   command(8'h12,0,0);
   for(j=0;j<DACS;j=j+1) if(codes[j*BITS+:BITS] !== (j==ch ? MID+ch:MID)) $fatal(1,"initial output routing mismatch");
   pulse(src,edge_fall); command(8'h20,0,0); // pre-arm edge must be ignored
   pulse(src,!edge_fall); repeat(3) @(negedge clk);
   if(dut.sample_state!==1) $fatal(1,"wrong polarity triggered");
   if(TRIGS>1) begin pulse((src+1)%TRIGS,edge_fall); repeat(3) @(negedge clk); end
   if(dut.sample_state!==1) $fatal(1,"wrong input triggered");
   seen=0; monitor=1; pulse(src,edge_fall);
   wait(dut.sample_state==1 && dut.sample_loops==2); @(negedge clk); monitor=0;
   if(seen!=6 || codes[ch*BITS+:BITS]!==MID+ch) $fatal(1,"repeat/return initial mismatch");
   command(8'h02,0,0);
   if(response[223:216]!==src || response[231:224]!==edge_fall) $fatal(1,"trigger status mismatch");
   if(response[87:72]!==MID+ch) $fatal(1,"status truncates high DAC bits");
   command(8'h22,0,0);
   if(codes[ch*BITS+:BITS]!==MAX-ch) $fatal(1,"custom abort code mismatch");
  end
  payload=0; payload[15:0]=512; payload[31:16]=512; payload[47:32]=1; payload[63:48]=1;
  payload[103:96]=1; payload[111:104]=DACS; payload[135:128]=1; command(8'h30,payload,3);
  payload[111:104]=0; payload[119:112]=TRIGS; command(8'h30,payload,3);
  payload[119:112]=0; payload[127:120]=2; command(8'h30,payload,3);
  payload=0; payload[15:0]=MAX+1; payload[31:16]=MID; payload[47:32]=1; payload[63:48]=1;
  command(8'h30,payload,3);
  payload[15:0]=MID; command(8'h30,payload,0);
  payload=0; payload[31:16]=MAX+1; payload[63:32]=1; command(8'h31,payload,3);
  if(BITS!=10) command(8'h10,0,3);
  $display("PASS expanded DAC=%0d trigger=%0d, initial, routing, both edges, idle, completion, abort, rejection",DACS,TRIGS); $finish;
 end
 initial begin #200000; $fatal(1,"timeout"); end
endmodule
