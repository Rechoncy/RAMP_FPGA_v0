`timescale 1ns/1ps
module ramp_top #(parameter DAC_COUNT=2,TRIGGER_COUNT=2,DAC_BITS=10,
 parameter [2047:0] BOOT_CONFIGS=0, parameter [16383:0] BOOT_FILES=0,
 parameter UART_BAUD=115200, parameter CORE_HZ=200000000)(
 input sys_clk, input [TRIGGER_COUNT-1:0] trigger_in, input uart_rx,
 output uart_tx,gnd0,gnd1,
 output reg [DAC_COUNT*DAC_BITS-1:0] dac_data, output [DAC_COUNT-1:0] dac_clk);
 wire clk_core,clk_dac,rst_core,rst_dac;
 clock_reset_adapter u_clock(sys_clk,clk_core,clk_dac,rst_core,rst_dac);
 wire [TRIGGER_COUNT-1:0] trigger_rise,trigger_fall;
 genvar i;
 generate for(i=0;i<TRIGGER_COUNT;i=i+1) begin : inputs
  trigger_sync u_sync(clk_core,rst_core,trigger_in[i],trigger_rise[i],trigger_fall[i]);
 end endgenerate
 wire [255:0] request;
 wire [239:0] response;
 wire request_valid,response_valid,response_ready;
 packet_link #(.BAUD(UART_BAUD),.CLK_HZ(CORE_HZ)) u_link(clk_core,rst_core,uart_rx,uart_tx,request,request_valid,response,response_valid,response_ready);
 wire [DAC_COUNT*DAC_BITS-1:0] dac_codes;
 parallel_controller #(.UART_AUTOBAUD(1),.CORE_HZ(CORE_HZ),.DAC_COUNT(DAC_COUNT),.TRIGGER_COUNT(TRIGGER_COUNT),.DAC_BITS(DAC_BITS),.BOOT_CONFIGS(BOOT_CONFIGS),.BOOT_FILES(BOOT_FILES)) u_controller(
  .clk(clk_core),.rst(rst_core),.request(request),.request_valid(request_valid),
  .response(response),.response_valid(response_valid),.response_ready(response_ready),
  .trigger_rise(trigger_rise),.trigger_fall(trigger_fall),.dac_codes(dac_codes));
 generate for(i=0;i<DAC_COUNT;i=i+1) begin : outputs
  always @(posedge clk_dac) begin
   if(rst_dac) dac_data[i*DAC_BITS+:DAC_BITS]<=(1 << (DAC_BITS-1));
   else dac_data[i*DAC_BITS+:DAC_BITS]<=dac_codes[i*DAC_BITS+:DAC_BITS];
  end
  ODDR #(.DDR_CLK_EDGE("SAME_EDGE")) u_clock(.C(clk_dac),.CE(1'b1),.D1(1'b1),.D2(1'b0),.R(1'b0),.S(1'b0),.Q(dac_clk[i]));
 end endgenerate
 assign gnd0=0; assign gnd1=0;
endmodule
