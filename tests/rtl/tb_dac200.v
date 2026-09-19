`timescale 1ns/1ps
// Uses the generated Clocking Wizard and Vivado UNISIM MMCM/ODDR models.
module tb_dac200 #(parameter DAC_COUNT=2, TRIGGER_COUNT=2, DAC_BITS=10);
 reg sys_clk=0; always #10 sys_clk=~sys_clk;
 wire uart_tx,gnd0,gnd1;
 wire [DAC_COUNT*DAC_BITS-1:0] data;
 wire [DAC_COUNT-1:0] dac_clk;
 reg [DAC_COUNT*DAC_BITS-1:0] stimulus;
 wire ramp_clk=dac_clk[0];
 real previous_edge,half_edge;
 integer i;
 ramp_top #(.DAC_COUNT(DAC_COUNT),.TRIGGER_COUNT(TRIGGER_COUNT),.DAC_BITS(DAC_BITS)) dut(
  sys_clk,{TRIGGER_COUNT{1'b0}},1'b1,uart_tx,gnd0,gnd1,data,dac_clk);
 initial begin
  wait(dut.rst_core===1'b0);
  repeat(5) @(posedge ramp_clk);
  previous_edge=$realtime;
  for(i=0;i<20;i=i+1) begin
   @(negedge ramp_clk);
   if($realtime-previous_edge<2.499 || $realtime-previous_edge>2.501)
    $fatal(1,"DAC high pulse must be 2.5 ns");
   #0.001;
   if(dac_clk!=={DAC_COUNT{1'b0}}) $fatal(1,"All DAC clock falling edges must align");
   @(posedge ramp_clk);
   if($realtime-previous_edge<4.999 || $realtime-previous_edge>5.001)
    $fatal(1,"DAC period must be 5 ns");
   previous_edge=$realtime;
   #0.001;
   if(dac_clk!=={DAC_COUNT{1'b1}}) $fatal(1,"DAC clocks must be phase aligned");
  end
  for(i=0;i<DAC_COUNT;i=i+1) stimulus[i*DAC_BITS+:DAC_BITS]=(1 << DAC_BITS)-1-i;
  force dut.dac_codes=stimulus;
  @(posedge dut.clk_core); #0.1;
  if(data!==stimulus) $fatal(1,"DAC data is not clocked at the shared core edge");
  release dut.dac_codes;
  $display("PASS DAC200: %0d DACs, generated MMCM and ODDR, 5 ns period, 2.5 ns high pulse, aligned channels, 200 MHz data registers",DAC_COUNT);
  $finish;
 end
 initial begin #20000; $fatal(1,"MMCM lock / clock simulation timeout"); end
endmodule
