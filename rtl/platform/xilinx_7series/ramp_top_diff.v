`timescale 1ns/1ps
// Differential board entry. Clocking Wizard must use PRIM_SOURCE=No_buffer:
// the only physical input buffer is here. Electrical attributes come from XDC.
module ramp_top_diff #(parameter DAC_COUNT=2,TRIGGER_COUNT=2,DAC_BITS=10,
 parameter [2047:0] BOOT_CONFIGS=0, parameter [16383:0] BOOT_FILES=0,
 parameter UART_BAUD=115200, parameter CORE_HZ=200000000)(
 input sys_clk_p,sys_clk_n, input [TRIGGER_COUNT-1:0] trigger_in, input uart_rx,
 output uart_tx,gnd0,gnd1,
 output [DAC_COUNT*DAC_BITS-1:0] dac_data, output [DAC_COUNT-1:0] dac_clk);
 wire reference_clock;
 IBUFDS u_reference_buffer(.I(sys_clk_p),.IB(sys_clk_n),.O(reference_clock));
 ramp_top #(.CORE_HZ(CORE_HZ),.DAC_COUNT(DAC_COUNT),.TRIGGER_COUNT(TRIGGER_COUNT),.DAC_BITS(DAC_BITS),.UART_BAUD(UART_BAUD),
  .BOOT_CONFIGS(BOOT_CONFIGS),.BOOT_FILES(BOOT_FILES)) core(
  .sys_clk(reference_clock),.trigger_in(trigger_in),.uart_rx(uart_rx),
  .uart_tx(uart_tx),.gnd0(gnd0),.gnd1(gnd1),.dac_data(dac_data),.dac_clk(dac_clk));
endmodule
