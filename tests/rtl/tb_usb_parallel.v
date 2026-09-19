`timescale 1ns/1ps
// The real controller requires response_ready on its request-admission cycle.
// Pin traffic is intentionally queued faster than replies complete.
module tb_usb_parallel;
 parameter COUNT=27;
 reg core_clk=0,usb_clk=0,rst=1;
 always #2.5 core_clk=~core_clk;
 always #8.333 usb_clk=~usb_clk;
 tri [7:0] data;
 reg rxf_n=1,txe_n=1;reg [7:0] input_data=0;
 wire oe_n,rd_n,wr_n,siwu_n;
 wire [255:0] request;wire [239:0] response;
 wire request_valid,response_valid,response_ready;
 wire [39:0] codes;
 reg [255:0] requests[0:COUNT-1],reply=0;
 integer sent=0,received=0,cycle=0,file_out,stalls=0;
 assign data=!oe_n && !rxf_n ? input_data:8'bz;
 usb_packet_link #(.FIFO_ADDR_BITS(4)) link(
  core_clk,rst,usb_clk,rst,data,rxf_n,txe_n,oe_n,rd_n,wr_n,siwu_n,
  request,request_valid,1'b1,response,response_valid,response_ready);
 parallel_controller #(.RUN_ADDR_BITS(6),.USB_FIFO_ENABLED(1)) control(
  .clk(core_clk),.rst(rst),.request(request),.request_valid(request_valid),
  .response(response),.response_valid(response_valid),.response_ready(response_ready),
  .trigger_rise(2'b0),.trigger_fall(2'b0),.dac_codes(codes));
 initial begin
  $readmemh("parallel_requests.hex",requests);
  file_out=$fopen("parallel_responses.hex","w");
  #71;rst=0;
  wait(received==COUNT*32);repeat(50)@(negedge core_clk);
  if(sent!=COUNT*32 || !stalls)$fatal(1,"missing request/backpressure coverage");
  if(codes[9:0]!==512 || codes[19:10]!==512 || codes[29:20]!==77 || codes[39:30]!==512)
   $fatal(1,"channel isolation or final abort code failed: %h",codes);
  $fclose(file_out);$display("PASS USB parallel: real INFO/batch/commit/status/arm/trigger/abort");$finish;
 end
 always @(posedge usb_clk)if(!rst)begin
  cycle=cycle+1;
  if(!rd_n && !rxf_n)sent=sent+1;
  if(!wr_n && !txe_n)begin
   if(!oe_n || !rd_n)$fatal(1,"USB bus contention");
   reply[(received%32)*8+:8]=data;
   received=received+1;
   if(received%32==0)$fdisplay(file_out,"%064h",reply);
  end
  if(!rxf_n && rd_n)stalls=stalls+1;
  #1;
  rxf_n=sent>=COUNT*32 || cycle%19<4;
  txe_n=cycle%113<48;
  if(sent<COUNT*32)input_data=requests[sent/32][(sent%32)*8+:8];
 end
 initial begin #5000000;$fatal(1,"USB/controller deadlock sent=%0d received=%0d state=%0d",sent,received,control.state);end
endmodule
