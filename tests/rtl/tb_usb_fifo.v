`timescale 1ns/1ps
// FT232H model: changes input flags/data after rising CLKOUT, independent core
// clock; stalls both directions while many packets are already queued.
module tb_usb_fifo;
 parameter real FLAG_DELAY=1.0;
 reg core_clk=0,usb_clk=0,rst=1;
 always #2.5 core_clk=~core_clk;
 always #8.333 usb_clk=~usb_clk;
 tri [7:0] data;
 reg rxf_n=1,txe_n=1;
 wire oe_n,rd_n,wr_n,siwu_n;
 wire [255:0] request;
 wire request_valid,response_ready;
 reg request_ready=0,response_valid=0;
 reg [239:0] response=0;
 reg [255:0] requests[0:11],responses[0:11];
 reg [7:0] stream[0:417];
 reg [7:0] input_data;
 integer sent=0,received=0,accepted=0,core_cycle=0,usb_cycle=0,wait_reply=0;
 integer i,j,position=0,last_oe=1,read_setup_cycles=0,stalled_reads=0,stalled_writes=0;
 reg [255:0] reply_buffer;
 reg previous_rd,previous_wr;
 assign data=!oe_n && !rxf_n ? input_data:8'bz;
 usb_packet_link #(.FIFO_ADDR_BITS(4)) dut(
  core_clk,rst,usb_clk,rst,data,rxf_n,txe_n,oe_n,rd_n,wr_n,siwu_n,
  request,request_valid,request_ready,response,response_valid,response_ready);
 initial begin
  $readmemh("requests.hex",requests);$readmemh("responses.hex",responses);
  // Two noise bytes and one bad CRC packet must produce no request.
  stream[0]=8'h55;stream[1]=8'h66;
  for(j=0;j<32;j=j+1)stream[2+j]=requests[0][j*8+:8];
  stream[33]=stream[33]^8'h01;
  for(i=0;i<12;i=i+1)for(j=0;j<32;j=j+1)stream[34+i*32+j]=requests[i][j*8+:8];
  #71;rst=0;
  wait(received==12*32);
  repeat(100)@(posedge core_clk);
  if(accepted!=12 || sent!=418 || stalled_reads==0 || stalled_writes==0)$fatal(1,"coverage sent=%0d accepted=%0d stalls=%0d/%0d",sent,accepted,stalled_reads,stalled_writes);
  $display("PASS USB: 12 CRC packets, bidirectional backpressure, 60/200 MHz CDC, bad CRC rejected");$finish;
 end
 always @(posedge core_clk)begin
  if(!rst)begin
   core_cycle<=core_cycle+1;
   request_ready<=core_cycle%37>12 && wait_reply==0 && !response_valid;
   if(request_valid && request_ready)begin
    if(accepted>=12 || request!==requests[accepted])$fatal(1,"request loss/corruption at %0d",accepted);
    response<=responses[accepted][239:0];accepted<=accepted+1;wait_reply<=120;request_ready<=0;
   end
   if(wait_reply>0)begin
    wait_reply<=wait_reply-1;
    if(wait_reply==1)response_valid<=1;
   end
   if(response_valid && response_ready)response_valid<=0;
  end
 end
 always @(posedge usb_clk)begin
  if(!rst)begin
   usb_cycle=usb_cycle+1;
   if(!oe_n)read_setup_cycles=read_setup_cycles+1;else read_setup_cycles=0;
   if(!rd_n)begin
    if(oe_n || read_setup_cycles<2 || !wr_n)$fatal(1,"OE/RD direction/setup violation");
    if(!rxf_n)sent=sent+1;
   end
   if(!wr_n && !txe_n)begin
    if(!oe_n || !rd_n)$fatal(1,"USB bus contention");
    if(data!==responses[received/32][(received%32)*8+:8])$fatal(1,"response mismatch byte %0d got %h",received,data);
    received=received+1;
   end
   if(!rxf_n && rd_n)stalled_reads=stalled_reads+1;
   if(txe_n && dut.usb_tx_valid)stalled_writes=stalled_writes+1;
   // Parameterized to both datasheet CLKOUT-to-output bounds (0 and 9 ns).
   #FLAG_DELAY;
   previous_rd=rd_n;previous_wr=wr_n;
   rxf_n=sent>=418 || usb_cycle%13<3;
   txe_n=usb_cycle%97<41;
   if(sent<418)input_data=stream[sent];
   #0.001;
   if(FLAG_DELAY>0 && (rd_n!==previous_rd || wr_n!==previous_wr))
    $fatal(1,"external flag combinationally changed RD#/WR#");
  end
 end
 initial begin #3000000;$fatal(1,"USB test timed out sent=%0d received=%0d",sent,received);end
endmodule
