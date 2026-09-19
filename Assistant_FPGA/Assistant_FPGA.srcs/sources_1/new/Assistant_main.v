`timescale 1ns / 1ps
//////////////////////////////////////////////////////////////////////////////////
// Company: 
// Engineer: 
// 
// Create Date: 2024/01/06 12:57:20
// Design Name: 
// Module Name: Assistant_main
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


module Assistant_main(
//input and output 
//
  input sys_clk,
//  input sys_rst_n,
  input dipole_ramp_trigger,          //F13
  input fm_trigger,                   //F14
//
  output gnd0,                        //F15
  output gnd1,                        //F16
  output [9:0] dipole_ramp,           //dipole ramp DA dipole_ramp_trigger control DA output;
  output [9:0] fm_value,              //frequency scan DA
  output dipole_ramp_clk,
  output da_fm_clk  
    );

assign dipole_ramp_clk = sys_clk;
assign da_fm_clk = sys_clk;
assign gnd0 = 0;
assign gnd1 = 0;

wire clk_100m;
wire clk_250m;
wire clk_100m_180deg;
wire clk_500m;

wire config_ready;
wire debug;
wire vio_rst_n;
// dipole ramp
wire [9:0] dipole_ramp_initial_value;
wire [9:0] dipole_ramp_end_value;
wire [9:0] dipole_ramp_step;
wire [28:0] dipole_ramp_total_time;
wire [5:0] ramp_type;

//fm trigger
wire [9:0] fm_initial_value;
wire [9:0] fm_scan_step;
wire [9:0] fm_scan_points;
//virtual input and output
vio_0 u_vio_0(
  .clk(sys_clk),
  .probe_out0(config_ready),
  .probe_out1(debug),
  .probe_out2(dipole_ramp_initial_value),
  .probe_out3(dipole_ramp_end_value),
  .probe_out4(dipole_ramp_step),
  .probe_out5(dipole_ramp_total_time),
  .probe_out6(ramp_type),
  
  .probe_out7(fm_initial_value),
  .probe_out8(fm_scan_step),
  .probe_out9(fm_scan_points),
  .probe_out10(vio_rst_n)
  
  );
dipole_ramp_main u_dipole_ramp_main(
//vio input  
  .sys_clk                    (sys_clk),
  .config_ready               (config_ready),
  .debug                      (debug),
  .vio_rst_n                   (vio_rst_n),      
  .dipole_ramp_initial_value  (dipole_ramp_initial_value),
  .dipole_ramp_end_value      (dipole_ramp_end_value),
  .dipole_ramp_step           (dipole_ramp_step),
  .dipole_ramp_total_time     (dipole_ramp_total_time),
  .ramp_type                  (ramp_type),

//FPGA input  
  .dipole_ramp_trigger        (dipole_ramp_trigger),
//FPGA output
  .dipole_ramp                (dipole_ramp)
);

frequency_modulator_main u_frequency_modulator_main(
  .sys_clk                    (sys_clk),
  .config_ready               (config_ready),
  .debug                      (debug),
  .fm_initial_value           (fm_initial_value),
  .fm_scan_step               (fm_scan_step),
  .fm_scan_points             (fm_scan_points),

//FPGA input
  .fm_trigger                 (fm_trigger),
//FPGA ouput
  .fm_value                   (fm_value)
);


// clock ip
ip_clk_wiz u_ip_clk_wiz(
  .sys_clk                   (sys_clk),
  .vio_rst_n                 (vio_rst_n),
  .clk_250m                  (clk_250m),
  .clk_100m                  (clk_100m),
  .clk_100m_180deg           (clk_100m_180deg),
  .clk_500m                  (clk_500m)
);
endmodule
