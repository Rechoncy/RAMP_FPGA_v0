// Copyright 1986-2020 Xilinx, Inc. All Rights Reserved.
// --------------------------------------------------------------------------------
// Tool Version: Vivado v.2020.2 (win64) Build 3064766 Wed Nov 18 09:12:45 MST 2020
// Date        : Tue Sep  9 18:40:42 2025
// Host        : DESKTOP-C069V43 running 64-bit major release  (build 9200)
// Command     : write_verilog -force -mode synth_stub {d:/vivado
//               file/Assistant_FPGA/Assistant_FPGA/Assistant_FPGA.srcs/sources_1/ip/vio_0/vio_0_stub.v}
// Design      : vio_0
// Purpose     : Stub declaration of top-level module interface
// Device      : xc7a35tfgg484-2
// --------------------------------------------------------------------------------

// This empty module with port declaration file causes synthesis tools to infer a black box for IP.
// The synthesis directives are for Synopsys Synplify support to prevent IO buffer insertion.
// Please paste the declaration into a Verilog source file or add the file as an additional source.
(* X_CORE_INFO = "vio,Vivado 2020.2" *)
module vio_0(clk, probe_out0, probe_out1, probe_out2, 
  probe_out3, probe_out4, probe_out5, probe_out6, probe_out7, probe_out8, probe_out9, 
  probe_out10)
/* synthesis syn_black_box black_box_pad_pin="clk,probe_out0[0:0],probe_out1[0:0],probe_out2[9:0],probe_out3[9:0],probe_out4[9:0],probe_out5[28:0],probe_out6[5:0],probe_out7[9:0],probe_out8[9:0],probe_out9[9:0],probe_out10[0:0]" */;
  input clk;
  output [0:0]probe_out0;
  output [0:0]probe_out1;
  output [9:0]probe_out2;
  output [9:0]probe_out3;
  output [9:0]probe_out4;
  output [28:0]probe_out5;
  output [5:0]probe_out6;
  output [9:0]probe_out7;
  output [9:0]probe_out8;
  output [9:0]probe_out9;
  output [0:0]probe_out10;
endmodule
