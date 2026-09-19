`timescale 1ns/1ps
// Exercise reply stalls that are longer than either controller transaction.
// Pin-level framing and CRC are covered by tb_dual_transport.
module tb_dual_router_backpressure;
 reg clk=0,rst=1;always #2.5 clk=~clk;
 reg [255:0] uart_request=0,usb_request=0;
 reg uart_valid=0,usb_valid=0,uart_ready=0,usb_ready=0;
 wire [239:0] uart_reply,usb_reply,response;
 wire uart_reply_valid,usb_reply_valid,usb_request_ready;
 wire [255:0] request;wire request_valid,response_valid,response_ready;
 wire [39:0] codes;
 integer admitted=0;reg [239:0] held_reply;
 dual_packet_router router(clk,rst,
  uart_request,uart_valid,uart_reply,uart_reply_valid,uart_ready,
  usb_request,usb_valid,usb_request_ready,usb_reply,usb_reply_valid,usb_ready,
  request,request_valid,response,response_valid,response_ready);
 parallel_controller #(.RUN_ADDR_BITS(6),.USB_FIFO_ENABLED(1),.SOURCE_TAGGED(1)) control(
  clk,rst,request,request_valid,response,response_valid,response_ready,2'b0,2'b0,codes);
 function [255:0] info(input [15:0] seq,input wire_crc_bit);
 begin info={15'b0,wire_crc_bit,192'b0,seq,8'h01,8'h01,16'h5aa5};end
 endfunction
 always @(posedge clk)if(!rst && request_valid)begin
  admitted=admitted+1;
  if(!response_ready)$fatal(1,"controller response slot unavailable at admission");
  if(request[47:32]!==admitted[15:0])$fatal(1,"arbitration ordering or duplicate request");
  if(request[240]!==(admitted!=2))$fatal(1,"source tag leaked wire CRC bit");
 end
 initial begin
  repeat(5)@(negedge clk);rst=0;
  usb_request=info(1,0);usb_valid=1;
  wait(usb_request_ready);@(negedge clk);usb_valid=0;
  // UART has no request-ready signal. Buffer its completed stop-and-wait frame.
  uart_request=info(2,1);uart_valid=1;@(negedge clk);uart_valid=0;
  wait(usb_reply_valid);@(negedge clk);held_reply=usb_reply;
  usb_request=info(3,0);usb_valid=1;
  repeat(80)begin
   @(negedge clk);
   if(!usb_reply_valid || usb_reply!==held_reply || uart_reply_valid || usb_request_ready || admitted!=1)
    $fatal(1,"USB reply did not remain stable under backpressure");
  end
  usb_ready=1;@(negedge clk);usb_ready=0;
  wait(uart_reply_valid);@(negedge clk);held_reply=uart_reply;
  repeat(80)begin
   @(negedge clk);
   if(!uart_reply_valid || uart_reply!==held_reply || usb_reply_valid || usb_request_ready || admitted!=2)
    $fatal(1,"UART reply was lost or next USB request admitted too early");
  end
  uart_ready=1;@(negedge clk);uart_ready=0;
  wait(usb_request_ready);@(negedge clk);usb_valid=0;
  wait(usb_reply_valid);@(negedge clk);
  if(usb_reply[47:32]!=3 || admitted!=3)$fatal(1,"queued USB request lost");
  // Reset abandons a stalled reply and must not leak it to either interface.
  rst=1;repeat(4)@(negedge clk);
  if(usb_reply_valid || uart_reply_valid || request_valid)$fatal(1,"valid asserted during reset");
  rst=0;repeat(20)@(negedge clk);
  if(usb_reply_valid || uart_reply_valid || request_valid)$fatal(1,"old reply survived reset");
  usb_request=info(4,0);usb_valid=1;usb_ready=1;
  wait(usb_request_ready);@(negedge clk);usb_valid=0;
  wait(usb_reply_valid);@(negedge clk);
  if(usb_reply[47:32]!=4 || usb_reply[55:48]!=0)$fatal(1,"reset recovery failed");
  $display("PASS dual router: queued arbitration, source tag, held replies, reset recovery");$finish;
 end
 initial begin #100000;$fatal(1,"dual router timeout");end
endmodule
