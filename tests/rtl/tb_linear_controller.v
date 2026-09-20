`timescale 1ns/1ps
module tb_linear_controller;
 parameter CORE_HZ=200000000;
 reg clk=0;always #(500000000.0/CORE_HZ) clk=~clk;
 reg rst=1,valid=0;reg [255:0] request=0;
 wire [239:0] response;wire response_valid;
 reg [1:0] rising=0,falling=0;wire [55:0] codes;
 parallel_controller #(.CORE_HZ(CORE_HZ),.DAC_BITS(14)) dut(clk,rst,request,valid,response,response_valid,1'b1,rising,falling,codes);
 reg [191:0] p;integer seq=0,i,j,k;reg bank_before;
 task tick;begin @(negedge clk);#0.1;end endtask
 task send;input [7:0] cmd;input [191:0] data;input [7:0] err;
 begin
  tick;seq=seq+1;request={16'b0,data,seq[15:0],cmd,8'h01,16'h5aa5};valid=1;
  tick;valid=0;wait(response_valid);#0.1;
  if(response[55:48]!==err || response[31:24] !== (cmd|8'h80)) $fatal(1,"cmd %h error %d expected %d",cmd,response[55:48],err);
  tick;
 end endtask
 task run;input integer index,code,ticks;
 begin p=0;p[15:0]=index;p[31:16]=code;p[63:32]=ticks;send('h31,p,0);end endtask
 task begin_fixed;input integer ch,linear,external_mode;
 begin
  send('h40,ch,0);p=0;p[15:0]=1000+ch;p[31:16]=700+ch;p[47:32]=2;p[63:48]=1;
  p[95:64]=100+ch;p[103:96]=external_mode;p[111:104]=ch;p[135:128]=1;
  send(linear ? 'h34:'h30,p,0);
 end endtask
 task begin_gate;
 begin
  p=0;p[15:0]=1000;p[31:16]=700;p[47:32]=2;p[63:48]=1;
  p[95:64]=200;p[127:112]=0;p[159:128]=32;send('h35,p,0);
 end endtask
 task begin_program;input integer linear;
 begin
  p=0;p[15:0]=1000;p[31:16]=700;p[47:32]=3;p[63:48]=3;p[95:64]=300;
  send(linear ? 'h52:'h50,p,0);
 end endtask
 task step;input integer index,kind,source,base,count,linear;input integer expected;
 begin
  p=0;p[7:0]=index;p[15:8]=kind;p[23:16]=source;p[47:32]=base;p[63:48]=count;
  p[79:64]=1;p[95:80]=0;p[127:96]=32;p[175:160]=1;p[191:176]=linear;send('h51,p,expected);
 end endtask
 task edge_r;input [1:0] mask;begin tick;rising=mask;tick;rising=0;end endtask
 task edge_f;input [1:0] mask;begin tick;falling=mask;tick;falling=0;end endtask
 task wait_step;input integer n;
 begin wait(dut.channels[0].engine.program_step==n && dut.channels[0].engine.program_prepared);tick;end endtask
 initial begin
  repeat(6)tick;rst=0;send(1,0,0);
  if(response[127:120]!=14 || response[167:152]!=2048 || response[175:168]!=4 ||
     response[207:200]!=35 || response[215:208]!=32 || response[223:216]!=1 || response[239:232]!=3)$fatal(1,"capabilities");
  if(response[87:56]!==CORE_HZ)$fatal(1,"INFO sample frequency mismatch");
  // The one-second gate return limit follows the selected core clock.
  p=0;p[15:0]=1000;p[31:16]=700;p[47:32]=2;p[63:48]=1;p[159:128]=CORE_HZ+1;
  send('h35,p,3);send('h33,p,3);
  // Old constant-code mode keeps one-clock descriptors and refuses new step flags.
  begin_fixed(0,0,0);run(0,20,1);run(1,30,1);send('h12,0,0);
  begin_fixed(0,1,0);p=0;p[31:16]=20;p[63:32]=31;send('h31,p,3);
  if(dut.channels[0].engine.sample_loaded!=0)$fatal(1,"short segment consumed a slot");
  run(0,0,32);run(1,1000,32);send('h12,0,0);
  bank_before=dut.channels[0].engine.active_bank;
  begin_gate;run(0,1,32);run(1,1000,32);send('h12,0,3);
  if(dut.channels[0].engine.active_bank!=bank_before)$fatal(1,"failed gate replaced active bank");
  begin_gate;run(0,0,32);run(1,1000,33);send('h12,0,3);
  begin_gate;run(0,0,32);run(1,999,32);send('h12,0,3);
  begin_program(0);step(0,0,0,0,2,1,3);
  begin_program(1);step(0,0,0,0,2,2,3);
  // A mixed sequence may carry short hold runs; linear ranges are checked at COMMIT.
  run(0,0,31);run(1,1000,32);run(2,444,80);
  step(0,0,0,0,2,1,0);step(1,0,1,2,1,0,0);step(2,1,0,0,2,1,0);send('h12,0,3);
  // Capacity is per bank, never the sum of active and pending banks.
  p=0;p[47:32]=2049;p[63:48]=1;p[135:128]=1;send('h34,p,3);
  // All four 14-bit engines start together from one group trigger.
  for(i=0;i<4;i=i+1)begin begin_fixed(i,1,0);run(0,0,32);run(1,1000+i,32);send('h12,0,0);end
  send('h41,16'h0f20,0);
  send('h41,16'h0f21,0);
  wait(dut.monitor[0][1:0]==3);repeat(5)tick;
  for(i=0;i<4;i=i+1)begin
   if(codes[i*14+:14]!=1000+i)$fatal(1,"parallel final code");
   send('h40,i,0);send(2,0,0);
   if(response[103:88]!=1 || response[215:208]!=3)$fatal(1,"linear status");
  end
  // Mixed event steps reuse one linear waveform pool range; each uses its own source.
  send('h40,0,0);begin_program(1);run(0,0,32);run(1,1000,32);run(2,444,80);
  step(0,0,0,0,2,1,0);step(1,0,1,2,1,0,0);step(2,1,0,0,2,1,0);
  send('h12,0,0);send('h20,0,0);wait_step(0);edge_r(1);repeat(8)tick;edge_r(1);
  wait_step(1);if(dut.channels[0].engine.program_ignored!=1)$fatal(1,"busy event accounting");
  edge_r(1);repeat(10)tick;if(dut.channels[0].engine.program_state!=1)$fatal(1,"wrong source advanced");
  edge_r(2);repeat(8)tick;if(codes[13:0]!=444)$fatal(1,"mixed constant-code step");
  wait_step(2);edge_r(1);repeat(48)tick;
  if(codes[13:0]!=0 || dut.channels[0].engine.linear_phase!=2)$fatal(1,"linear gate hold");
  edge_f(1);wait_step(0);
  if(dut.channels[0].engine.program_loops!=1 || codes[13:0]!=1000)$fatal(1,"sequence loop/rearm");
  send('h22,0,0);repeat(5)tick;if(codes[13:0]!=700)$fatal(1,"sequence abort code");
  // Standalone normal gate auto-rearms and an early fall uses the legacy return path.
  begin_gate;run(0,0,32);run(1,1000,32);send('h12,0,0);send('h20,0,0);
  edge_r(1);repeat(7)tick;edge_f(1);repeat(100)tick;
  if(!dut.monitor[0][2] || codes[13:0]!=1000)$fatal(1,"early fall auto rearm");
  send('h22,0,0);if(codes[13:0]!=700)$fatal(1,"gate abort");
  $display("PASS linear controller: protocol rejection, inactive bank validation, 4x14-bit group synchronization, mixed event reuse/routing/busy rules, gate early fall, abort");$finish;
 end
 // Shared software start must be the same clock across all four channels.
 always @(negedge clk) if(!rst && dut.channels[0].engine.soft_trigger) begin
  if(!dut.channels[1].engine.soft_trigger || !dut.channels[2].engine.soft_trigger || !dut.channels[3].engine.soft_trigger)$fatal(1,"group trigger skew");
 end
 initial begin #5000000;$fatal(1,"timeout");end
endmodule
