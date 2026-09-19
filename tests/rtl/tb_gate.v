`timescale 1ns/1ps
module tb_gate;
 parameter BITS=10;
 localparam MID=1 << (BITS-1), MAX=(1 << BITS)-1;
 reg clk=0; always #2.5 clk=~clk;
 reg rst=1,valid=0;
 reg [1:0] rising=0,falling=0;
 reg [255:0] request=0;
 wire [239:0] response; wire response_valid;
 wire [2*BITS-1:0] codes;
 reg [191:0] payload;
 integer seq=0,i,k,old_code,captured,expected,fall_length;
 reg full_capture=0;
 ramp_controller #(.DAC_BITS(BITS)) dut(clk,rst,request,valid,response,response_valid,1'b1,rising,falling,codes,);
 task tick; begin @(negedge clk); #0.1; end endtask
 task command; input [7:0] cmd; input [191:0] data; input [7:0] error;
  begin
   tick; seq=seq+1; request=0; request[15:0]=16'h5aa5;
   request[23:16]=1; request[31:24]=cmd; request[47:32]=seq; request[239:48]=data; valid=1;
   tick; valid=0; wait(response_valid); #0.1;
   if(response[55:48]!==error) $fatal(1,"command=%h error=%d expected=%d",cmd,response[55:48],error);
   tick;
  end
 endtask
 task load; input integer initial_value,target_value,return_length;
  begin
   payload=0; payload[15:0]=initial_value; payload[31:16]=7; payload[47:32]=4; payload[63:48]=2;
   payload[95:64]=123; payload[103:96]=1; payload[111:104]=0;
   payload[127:112]=target_value; payload[159:128]=return_length;
   command(8'h32,payload,0);
   payload=0; payload[31:16]=initial_value; payload[63:32]=2; command(8'h31,payload,0);
   payload=0; payload[15:0]=1; payload[31:16]=full_capture ? target_value:(initial_value+target_value)/2; payload[63:32]=8; command(8'h31,payload,0);
   payload=0; payload[15:0]=2; payload[31:16]=target_value; payload[63:32]=1; command(8'h31,payload,0);
   payload=0; payload[15:0]=3; payload[31:16]=(initial_value+target_value)/2; payload[63:32]=return_length>1 ? return_length-1:1; command(8'h31,payload,0);
   command(8'h12,0,0); command(8'h20,0,0);
  end
 endtask
 task start;
  begin rising[0]=1; tick; rising=0; if(dut.gate_phase!==1 || dut.sample_state!==2) $fatal(1,"did not enter"); end
 endtask
 initial begin
  repeat(5) tick; rst=0;
  load(MID,0,9);
  // Wrong input and a fall without a rise must not start.
  rising[1]=1; falling[0]=1; tick; rising=0; falling=0;
  repeat(6) tick;
  if(dut.sample_state!==1 || codes[BITS+:BITS]!==MID) $fatal(1,"spurious start");
  for(k=0;k<3;k=k+1) begin
   start;
   for(i=0;i<10;i=i+1) begin
    if(codes[BITS+:BITS] !== (i<2 ? MID:MID/2)) $fatal(1,"rise sample %0d code %0d",i,codes[BITS+:BITS]);
    if(codes[0+:BITS]!==MID) $fatal(1,"unselected DAC changed");
    tick;
   end
   if(dut.gate_phase!==2 || codes[BITS+:BITS]!==0) $fatal(1,"hold missing");
   rising[0]=1; tick; rising=0;
   repeat(40) begin tick; if(codes[BITS+:BITS]!==0 || dut.gate_phase!==2) $fatal(1,"hold has timeout"); end
   falling[1]=1; tick; falling=0;
   if(dut.gate_phase!==2) $fatal(1,"wrong fall input");
   falling[0]=1; tick; falling=0;
   for(i=0;i<9;i=i+1) begin
    if(codes[BITS+:BITS] !== (i==0 ? 0:MID/2)) $fatal(1,"return sample %0d",i);
    tick;
   end
   if(dut.sample_state!==1 || codes[BITS+:BITS]!==MID || dut.gate_loops!==k+1) $fatal(1,"automatic rearm");
   repeat(8) tick;
  end
  command(8'h02,0,0);
  if(response[239:232]!==128) $fatal(1,"gate status flag");
  command(8'h23,0,0);
  // Early return in both code directions, fractional steps and one-clock return.
  for(k=0;k<8;k=k+1) begin
   full_capture=k>=4;
   fall_length=k==3 ? 1:k>=4 ? 33:9;
   load(k%2 ? 0:MID,k%2 ? MAX:0,fall_length);
   start; repeat(3) tick; captured=codes[BITS+:BITS];
   falling[0]=1; tick; falling=0;
   if(codes[BITS+:BITS]!==captured || dut.gate_phase!==4) $fatal(1,"early fall jumps");
   for(i=0;i<BITS+1;i=i+1) begin
    tick;
    if(codes[BITS+:BITS]!==captured) $fatal(1,"division changed output");
   end
   if(dut.gate_phase!==5) $fatal(1,"division latency");
   for(i=1;i<=fall_length;i=i+1) begin
    tick;
    expected=k%2 ? captured-captured*i/fall_length:captured+(MID-captured)*i/fall_length;
    if(codes[BITS+:BITS]!==expected) $fatal(1,"linear sample %0d got %0d expected %0d",i,codes[BITS+:BITS],expected);
   end
   if(dut.sample_state!==1) $fatal(1,"early return not rearmed");
   command(8'h22,0,0);
   if(codes[BITS+:BITS]!==7 || dut.sample_state!==0) $fatal(1,"abort safe");
  end
  full_capture=0;
  // Falling exactly at end of rise must use normal return, no divider.
  load(MID,0,9); start; repeat(9) tick; falling[0]=1; tick; falling=0;
  if(dut.gate_phase!==3 || codes[BITS+:BITS]!==0) $fatal(1,"boundary falling edge");
  command(8'h22,0,0);
  // Long return divisor exceeds the width of the captured DAC difference.
  load(0,MAX,200000000); start; repeat(3) tick; captured=codes[BITS+:BITS];
  falling[0]=1; tick; falling=0; repeat(BITS+1) tick;
  if(dut.u_gate.step_q!==0 || dut.u_gate.step_r!==captured) $fatal(1,"wide divisor truncated");
  repeat(30) begin tick; if(codes[BITS+:BITS]!==captured) $fatal(1,"long return early step"); end
  command(8'h22,0,0);
  // Minimal two-entry table, each phase lasts a single clock.
  payload=0; payload[15:0]=MID; payload[31:16]=7; payload[47:32]=2; payload[63:48]=1;
  payload[103:96]=1; payload[127:112]=MAX; payload[159:128]=1; command(8'h32,payload,0);
  payload=0; payload[31:16]=MID; payload[63:32]=1; command(8'h31,payload,0);
  command(8'h12,0,4); // Cannot commit an incomplete table.
  payload[15:0]=1; payload[31:16]=MAX; command(8'h31,payload,0);
  command(8'h12,0,0); command(8'h20,0,0); start; tick;
  if(dut.gate_phase!==2 || codes[BITS+:BITS]!==MAX) $fatal(1,"one-clock rise");
  falling[0]=1; tick; falling=0;
  if(dut.gate_phase!==3 || codes[BITS+:BITS]!==MAX) $fatal(1,"one-clock return start");
  tick; if(dut.sample_state!==1 || codes[BITS+:BITS]!==MID) $fatal(1,"one-clock completion");
  command(8'h22,0,0);
  // Capacity boundary and continuous one-clock descriptors across both phases.
  payload=0; payload[15:0]=MID; payload[31:16]=7; payload[47:32]=8192; payload[63:48]=4095;
  payload[103:96]=1; payload[127:112]=MAX; payload[159:128]=4097; command(8'h32,payload,0);
  for(i=0;i<8192;i=i+1) begin
   payload=0; payload[15:0]=i; payload[31:16]=(i*17+3)&MAX; payload[63:32]=1; command(8'h31,payload,0);
  end
  command(8'h12,0,0); command(8'h20,0,0); start;
  for(i=0;i<4095;i=i+1) begin
   if(codes[BITS+:BITS]!==((i*17+3)&MAX)) $fatal(1,"capacity rise sample %0d",i);
   tick;
  end
  if(dut.gate_phase!==2 || codes[BITS+:BITS]!==MAX) $fatal(1,"capacity hold");
  repeat(20) tick;
  falling[0]=1; tick; falling=0;
  for(i=4095;i<8192;i=i+1) begin
   if(codes[BITS+:BITS]!==((i*17+3)&MAX)) $fatal(1,"capacity return sample %0d",i);
   tick;
  end
  if(dut.sample_state!==1 || codes[BITS+:BITS]!==MID) $fatal(1,"capacity completion");
  command(8'h22,0,0);
  payload=0; command(8'h32,payload,3);
  $display("PASS gate %0d-bit rise/hold/fall, routing, rearm, early linear, boundary, abort",BITS); $finish;
 end
 initial begin #2000000; $fatal(1,"timeout"); end
endmodule
