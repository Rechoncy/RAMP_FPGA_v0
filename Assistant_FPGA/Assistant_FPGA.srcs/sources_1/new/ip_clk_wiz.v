`timescale 1ns / 1ps
//////////////////////////////////////////////////////////////////////////////////
// Company: 
// Engineer: 
// 
// Create Date: 2025/09/09 18:13:57
// Design Name: 
// Module Name: ip_clk_wiz
// Project Name: 
// Target Devices: 
// Tool Versions: 
// Description: 
// 
// Dependencies: 
// 
// Revision:
// Revision 0.01 - File Created
// Additional Comments:
// 
//////////////////////////////////////////////////////////////////////////////////


module ip_clk_wiz(
    input sys_clk,
    input vio_rst_n,             //µÕµÁ∆Ω
    output clk_250m,
    output clk_100m,
    output clk_100m_180deg,
    output clk_500m
    );
 
wire locked; 
wire rst_n;

assign rst_n = vio_rst_n & locked;
    
clk_wiz_0 instance_name
(
// Clock out ports
.clk_out1      (clk_250m),     // output clk_out1
.clk_out2      (clk_100m),     // output clk_out2
.clk_out3      (clk_100m_180deg),     // output clk_out3
.clk_out4      (clk_500m),     // output clk_out4
// Status and control signals
.reset         (!vio_rst_n), // input reset
.locked        (locked),       // output locked
// Clock in ports
.clk_in1        (sys_clk));      // input clk_in1
endmodule
