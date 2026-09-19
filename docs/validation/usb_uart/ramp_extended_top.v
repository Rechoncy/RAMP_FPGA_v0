`timescale 1ns/1ps
module ramp_extended_top #(parameter [2047:0] BOOT_CONFIGS=0, parameter [16383:0] BOOT_FILES=0)(input sys_clk,input [1:0] trigger_in,input uart_rx,
 output uart_tx,gnd0,gnd1,output reg [39:0] dac_data,
 output [3:0] dac_clk,
 input usb_clk, inout [7:0] usb_data,
 input usb_rxf_n,usb_txe_n,output usb_rd_n,usb_wr_n,usb_oe_n,usb_siwu_n);
 wire clk_core,clk_dac,rst_core,rst_dac;
 clock_reset_adapter u_clock(sys_clk,clk_core,clk_dac,rst_core,rst_dac);
 wire [1:0] trigger_rise,trigger_fall;
 genvar i;
 generate for(i=0;i<2;i=i+1) begin: inputs
  trigger_sync u_sync(clk_core,rst_core,trigger_in[i],trigger_rise[i],trigger_fall[i]);
 end endgenerate
 wire [255:0] request;wire [239:0] response;
 wire request_valid,response_valid,response_ready;
 (* ASYNC_REG="TRUE" *) reg [2:0] usb_reset=3'b111;
 always @(posedge usb_clk or posedge rst_core)
  if(rst_core)usb_reset<=3'b111;else usb_reset<={usb_reset[1:0],1'b0};
 wire [255:0] uart_request,usb_request;
 wire [239:0] uart_response,usb_response;
 wire uart_request_valid,uart_response_valid,uart_response_ready;
 wire usb_request_valid,usb_request_ready,usb_response_valid,usb_response_ready;
 packet_link #(.BAUD(115200)) u_uart(clk_core,rst_core,uart_rx,uart_tx,
  uart_request,uart_request_valid,uart_response,uart_response_valid,uart_response_ready);
 usb_packet_link u_usb(.core_clk(clk_core),.core_rst(rst_core),.usb_clk(usb_clk),.usb_rst(usb_reset[2]),
  .usb_data(usb_data),.usb_rxf_n(usb_rxf_n),.usb_txe_n(usb_txe_n),
  .usb_rd_n(usb_rd_n),.usb_wr_n(usb_wr_n),.usb_oe_n(usb_oe_n),.usb_siwu_n(usb_siwu_n),
  .request(usb_request),.request_valid(usb_request_valid),.request_ready(usb_request_ready),
  .response(usb_response),.response_valid(usb_response_valid),.response_ready(usb_response_ready));
 dual_packet_router u_router(.clk(clk_core),.rst(rst_core),
  .uart_request(uart_request),.uart_request_valid(uart_request_valid),
  .uart_response(uart_response),.uart_response_valid(uart_response_valid),.uart_response_ready(uart_response_ready),
  .usb_request(usb_request),.usb_request_valid(usb_request_valid),.usb_request_ready(usb_request_ready),
  .usb_response(usb_response),.usb_response_valid(usb_response_valid),.usb_response_ready(usb_response_ready),
  .request(request),.request_valid(request_valid),.response(response),
  .response_valid(response_valid),.response_ready(response_ready));
 wire [39:0] dac_codes;
 parallel_controller #(.DAC_COUNT(4),.TRIGGER_COUNT(2),.DAC_BITS(10),
  .RUN_ADDR_BITS(11),.USB_FIFO_ENABLED(1),.SOURCE_TAGGED(1),
  .BOOT_CONFIGS(BOOT_CONFIGS),.BOOT_FILES(BOOT_FILES)) u_controller(
  .clk(clk_core),.rst(rst_core),.request(request),.request_valid(request_valid),
  .response(response),.response_valid(response_valid),.response_ready(response_ready),
  .trigger_rise(trigger_rise),.trigger_fall(trigger_fall),.dac_codes(dac_codes));
 generate for(i=0;i<4;i=i+1) begin: outputs
  always @(posedge clk_dac)
   if(rst_dac)dac_data[i*10+:10]<=512;
   else dac_data[i*10+:10]<=dac_codes[i*10+:10];
  ODDR #(.DDR_CLK_EDGE("SAME_EDGE")) u_clock(.C(clk_dac),.CE(1'b1),.D1(1'b1),.D2(1'b0),.R(1'b0),.S(1'b0),.Q(dac_clk[i]));
 end endgenerate
 assign gnd0=0;assign gnd1=0;
endmodule
