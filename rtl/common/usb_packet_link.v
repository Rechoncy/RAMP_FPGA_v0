`timescale 1ns/1ps
module usb_packet_link #(parameter FIFO_ADDR_BITS=9)(
 input core_clk,core_rst,usb_clk,usb_rst,
 inout [7:0] usb_data,input usb_rxf_n,usb_txe_n,
 output usb_oe_n,usb_rd_n,usb_wr_n,usb_siwu_n,
 output [255:0] request,output request_valid,input request_ready,
 input [239:0] response,input response_valid,output response_ready);
 wire reset_async=core_rst | usb_rst;
 (* ASYNC_REG="TRUE" *)reg [2:0] core_reset_pipe,usb_reset_pipe;
 wire core_reset=core_reset_pipe[2],usb_reset=usb_reset_pipe[2];
 always @(posedge core_clk or posedge reset_async)
  if(reset_async)core_reset_pipe<=3'b111;else core_reset_pipe<={core_reset_pipe[1:0],1'b0};
 always @(posedge usb_clk or posedge reset_async)
  if(reset_async)usb_reset_pipe<=3'b111;else usb_reset_pipe<={usb_reset_pipe[1:0],1'b0};
 wire [7:0] usb_rx_data,core_rx_data,usb_tx_data,core_tx_data;
 wire usb_rx_valid,usb_rx_ready,core_rx_valid,core_rx_ready;
 wire usb_tx_valid,usb_tx_ready,core_tx_valid,core_tx_ready;
 reg [7:0] packet_rx_data;
 reg packet_rx_valid;
 wire packet_rx_ready;
 // Isolate the asynchronous FIFO's distributed-RAM read mux from the 200 MHz
 // CRC/parser logic. This elastic stage may replace a consumed byte each clock
 // and retains it while the command/reply path applies backpressure.
 assign core_rx_ready=!core_reset && (!packet_rx_valid || packet_rx_ready);
 always @(posedge core_clk or posedge core_reset)begin
  if(core_reset)begin packet_rx_valid<=0;packet_rx_data<=0;end
  else if(core_rx_ready)begin
   packet_rx_valid<=core_rx_valid;
   if(core_rx_valid)packet_rx_data<=core_rx_data;
  end
 end
 ft245_sync physical(usb_clk,usb_reset,usb_data,usb_rxf_n,usb_txe_n,
  usb_oe_n,usb_rd_n,usb_wr_n,usb_siwu_n,
  usb_rx_data,usb_rx_valid,usb_rx_ready,usb_tx_data,usb_tx_valid,usb_tx_ready);
 usb_async_fifo #(.ADDR_BITS(FIFO_ADDR_BITS)) incoming(
  usb_clk,usb_reset,usb_rx_data,usb_rx_valid,usb_rx_ready,
  core_clk,core_reset,core_rx_data,core_rx_valid,core_rx_ready);
 usb_async_fifo #(.ADDR_BITS(FIFO_ADDR_BITS)) outgoing(
  core_clk,core_reset,core_tx_data,core_tx_valid,core_tx_ready,
  usb_clk,usb_reset,usb_tx_data,usb_tx_valid,usb_tx_ready);
 usb_byte_packet_link packets(core_clk,core_reset,packet_rx_data,packet_rx_valid,packet_rx_ready,
  core_tx_data,core_tx_valid,core_tx_ready,request,request_valid,request_ready,
  response,response_valid,response_ready);
endmodule
