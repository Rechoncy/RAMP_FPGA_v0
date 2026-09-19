`timescale 1ns/1ps
// Immutable-parent equivalence; every response bit is checked even while invalid.
module tb_parallel_cycle_compatibility;
 parameter BITS=14;
 localparam MID=1<<(BITS-1);
 reg clk=0;always #2.5 clk=~clk;
 reg rst=1,request_valid=0,response_ready=1;
 reg [255:0] request=0;
 reg [1:0] rising=0,falling=0;
 wire [239:0] response,old_response;
 wire response_valid,old_valid;
 wire [4*BITS-1:0] codes,old_codes;
 parallel_controller #(.DAC_BITS(BITS)) current(
  clk,rst,request,request_valid,response,response_valid,response_ready,rising,falling,codes);
 frozen_parallel_controller #(.DAC_BITS(BITS)) frozen(
  clk,rst,request,request_valid,old_response,old_valid,response_ready,rising,falling,old_codes);

 integer clocks=0,cache_hits=0,acks=0,busy_requests=0,stalled_clocks=0,seq=0;
 integer i,j,guard;
 reg [191:0] p;
 reg [255:0] last_request=0,saved_request=0;
 reg [239:0] saved_response=0;
 reg [31:0] random_word=32'h5d772001;
 always @(posedge clk) begin
  // Count the events consumed at this edge, before nonblocking assignments.
  if(!rst) begin
   if(frozen.state==2 && frozen.cache_valid && (&frozen.equal_chunks)) cache_hits=cache_hits+1;
   if(request_valid && frozen.state!=0) busy_requests=busy_requests+1;
   if(frozen.state==7 && !response_ready) stalled_clocks=stalled_clocks+1;
  end
  #0.2;clocks=clocks+1;
  if({response,response_valid,codes}!=={old_response,old_valid,old_codes})
   $fatal(1,"parallel outputs differ clock=%0d bits=%0d state=%0d/%0d valid=%0b/%0b response=%h/%h codes=%h/%h",
    clocks,BITS,current.state,frozen.state,response_valid,old_valid,response,old_response,codes,old_codes);
  if(!rst && response_valid)acks=acks+1;
 end
 task tick;begin @(posedge clk);#0.1;end endtask
 task transact;
  input [7:0] command;
  input [191:0] payload;
  input [15:0] sequence_id;
  input [7:0] error_expected;
  input integer stall_length,noise;
  reg [255:0] packet;
  reg [239:0] held;
  integer wait_clocks;
  begin
   packet={16'b0,payload,sequence_id,command,8'h01,16'h5aa5};
   @(negedge clk);request=packet;request_valid=1;response_ready=1;
   tick;
   @(negedge clk);request_valid=(noise!=0);response_ready=(stall_length==0);
   // Retry while the parent is busy. A different packet must also be ignored.
   if(noise==2)request=packet ^ (256'd1<<48);
   if(noise!=0)begin
    repeat(2)tick;
    @(negedge clk);request_valid=0;
   end
   if(stall_length!=0)begin
    wait_clocks=0;
    while(frozen.state!=7 && wait_clocks<10000)begin tick;wait_clocks=wait_clocks+1;end
    if(frozen.state!=7)$fatal(1,"response failed to reach pending state");
    held=response;
    repeat(stall_length)begin
     tick;
     if(response_valid || response!==held)$fatal(1,"pending response changed under backpressure");
    end
    @(negedge clk);response_ready=1;
   end
   wait_clocks=0;
   while(!response_valid && wait_clocks<10000)begin tick;wait_clocks=wait_clocks+1;end
   if(!response_valid)$fatal(1,"command response timeout");
   if(response[55:48]!==error_expected || response[31:24]!==(command|8'h80) ||
      response[47:32]!==sequence_id)
    $fatal(1,"bad response cmd=%h sequence=%0d error=%0d expected=%0d",command,sequence_id,response[55:48],error_expected);
   last_request=packet;
   @(negedge clk);request_valid=0;response_ready=1;
  end
 endtask
 task send;
  input [7:0] command;input [191:0] payload;input [7:0] expected;
  begin
   seq=seq+1;
   transact(command,payload,seq[15:0],expected,(seq%4==0)?7:0,seq%3);
  end
 endtask
 task run;
  input integer index,value,duration;
  begin p=0;p[15:0]=index;p[31:16]=value;p[63:32]=duration;send('h31,p,0);end
 endtask
 task load_fixed;
  input integer channel,linear;
  begin
   send('h40,channel,0);
   p=0;p[15:0]=MID+channel;p[31:16]=MID-40-channel;p[47:32]=2;
   p[63:48]=0;p[95:64]=100+channel;p[111:104]=channel;p[135:128]=1;
   send(linear?'h34:'h30,p,0);
   run(0,MID-50-channel,linear?32:1);
   run(1,MID+channel,linear?35:1);
   send('h12,0,0);
  end
 endtask
 task load_gate;
  input integer channel,linear;
  begin
   send('h40,channel,0);
   p=0;p[15:0]=MID+channel;p[31:16]=MID-40-channel;p[47:32]=2;p[63:48]=1;
   p[95:64]=200+channel;p[103:96]=channel;p[111:104]=channel%2;
   p[127:112]=MID-50-channel;p[159:128]=64;
   send(linear?'h35:'h32,p,0);
   run(0,MID-50-channel,64);run(1,MID+channel,64);send('h12,0,0);
  end
 endtask
 task edge_r;
  input [1:0] mask;
  begin @(negedge clk);rising=mask;tick;@(negedge clk);rising=0;end
 endtask
 task edge_f;
  input [1:0] mask;
  begin @(negedge clk);falling=mask;tick;@(negedge clk);falling=0;end
 endtask
 initial begin
  repeat(3)tick;@(negedge clk);rst=0;
  // New request, exact replay, and same sequence with different payload/command.
  send(1,0,0);saved_request=last_request;saved_response=response;
  transact(1,0,saved_request[47:32],0,11,1);
  if(response!==saved_response)$fatal(1,"capability retry did not return cached data");
  transact('h40,1,16'h7fff,0,0,2);
  transact('h40,2,16'h7fff,0,5,1);
  if(current.selected!=2)$fatal(1,"same-sequence changed payload reused stale cache");
  transact(2,0,16'h7fff,0,0,0);
  if(response[191:184]!=2)$fatal(1,"same-sequence changed command reused stale cache");
  send('h40,4,3);send('h41,16'h1020,3);send('h41,16'h0020,3);
  send('hff,0,1);

  // Old one-clock held samples and new interpolation share atomic group control.
  load_fixed(0,0);send('h41,16'h0f20,4);
  for(i=1;i<4;i=i+1)load_fixed(i,i%2);
  send('h41,16'h0f20,0);repeat(100)tick;
  send('h41,16'h0f21,0);saved_request=last_request;saved_response=response;
  repeat(100)tick;
  // Replaying GROUP START must not redispatch to already-busy engines.
  transact('h41,saved_request[239:48],saved_request[47:32],0,13,1);
  if(response!==saved_response)$fatal(1,"group retry changed cached response");
  send('h41,16'h0f20,2);send('h41,16'h0f21,2);
  send('h41,16'h0522,0);repeat(50)tick;
  if(codes[0+:BITS]!=MID-40 || codes[2*BITS+:BITS]!=MID-42)$fatal(1,"masked group abort");
  if(current.monitor[1][1:0]!=2 || current.monitor[3][1:0]!=2)$fatal(1,"masked abort affected other channels");
  for(i=0;i<4;i=i+1)begin send('h40,i,0);send(2,0,0);end
  send('h41,16'h0f22,0);

  // Two trigger inputs, mixed gate playback, ordinary and early falling edges.
  for(i=0;i<4;i=i+1)load_gate(i,i%2);
  send('h41,16'h0f20,0);repeat(100)tick;
  edge_r(1);repeat(90)tick;edge_r(2);repeat(7)tick;edge_f(2);
  repeat(150)tick;
  if(codes[0+:BITS]!=MID-50 || codes[2*BITS+:BITS]!=MID-52)$fatal(1,"shared gate source hold");
  edge_f(1);repeat(150)tick;
  for(i=0;i<4;i=i+1)if(codes[i*BITS+:BITS]!=MID+i)$fatal(1,"gate return after routed edges");
  edge_r(3);repeat(85)tick;edge_f(3);repeat(150)tick;
  send('h41,16'h0f22,0);

  // Reset while a completed response is held by backpressure clears the cache.
  @(negedge clk);request={16'b0,192'b0,16'h9999,8'h01,8'h01,16'h5aa5};request_valid=1;
  tick;@(negedge clk);request_valid=0;response_ready=0;
  guard=0;while(frozen.state!=7 && guard<1000)begin tick;guard=guard+1;end
  if(frozen.state!=7)$fatal(1,"reset case failed to reach blocked response");
  @(negedge clk);rst=1;tick;
  if(response!==240'b0 || response_valid)$fatal(1,"reset did not clear pending output");
  @(negedge clk);rst=0;response_ready=1;
  transact(1,0,16'h9999,0,3,0);

  // Bounded asynchronous traffic: retries/noise while busy, random backpressure,
  // trigger activity, and one-clock resets. No unbounded configuration uploads.
  for(i=0;i<4000;i=i+1)begin
   @(negedge clk);
   random_word={random_word[30:0],random_word[31]^random_word[21]^random_word[1]^random_word[0]};
   rst=(i==1201 || i==2501);response_ready=(random_word[1:0]!=0);
   request_valid=(random_word[4:2]==0);
   rising=(random_word[10:6]==0)?random_word[12:11]:0;
   falling=(random_word[17:13]==0)?random_word[19:18]:0;
   if(request_valid)begin
    case(random_word[22:20])
     0:request={16'b0,192'b0,i[15:0],8'h01,8'h01,16'h5aa5};
     1:request={16'b0,192'b0,i[15:0],8'h02,8'h01,16'h5aa5};
     2:request={16'b0,184'b0,6'b0,random_word[24:23],i[15:0],8'h40,8'h01,16'h5aa5};
     3:request={16'b0,176'b0,4'b0,random_word[26:23],8'h22,i[15:0],8'h41,8'h01,16'h5aa5};
     4:request={16'b0,192'b0,i[15:0],8'h20,8'h01,16'h5aa5};
     5:request={16'b0,192'b0,i[15:0],8'h21,8'h01,16'h5aa5};
     6:request={16'b0,192'b0,i[15:0],8'hff,8'h01,16'h5aa5};
     7:request=request;
    endcase
   end
   tick;
  end
  @(negedge clk);rst=0;request_valid=0;response_ready=1;rising=0;falling=0;
  repeat(100)tick;
  if(cache_hits<3 || busy_requests<10 || stalled_clocks<30 || acks<80)
   $fatal(1,"insufficient cache/busy/backpressure/response coverage: %0d/%0d/%0d/%0d",cache_hits,busy_requests,stalled_clocks,acks);
  #0.2;
  $display("PASS frozen parallel output comparison bits=%0d clocks=%0d cache=%0d busy=%0d stalled=%0d replies=%0d",BITS,clocks,cache_hits,busy_requests,stalled_clocks,acks);
  $finish;
 end
 initial begin #1000000;$fatal(1,"parallel comparison timeout");end
endmodule
