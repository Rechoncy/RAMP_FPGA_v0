`timescale 1ns/1ps
module clock_reset_adapter(input sys_clk,output clk_core,output clk_dac,output rst_core,output rst_dac);
 wire locked;
 // Clocking Wizard's startup circuit handles initial MMCM lock. Business reset
 // is independent; loss of lock holds the shared clock domain in reset.
 clk_core_ip u_ip(.clk_in1(sys_clk),.reset(1'b0),.clk_out1(clk_core),.locked(locked));
 (* ASYNC_REG="TRUE" *) reg [2:0] core_sync=3'b111;
 always @(posedge clk_core or negedge locked)
  if(!locked) core_sync<=3'b111; else core_sync<={core_sync[1:0],1'b0};
 assign clk_dac=clk_core;
 assign rst_core=core_sync[2]; assign rst_dac=core_sync[2];
endmodule
