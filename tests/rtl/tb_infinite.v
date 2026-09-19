`timescale 1ns/1ps
module tb_infinite;
 parameter BITS=10;
 parameter EXTERNAL=0;
 parameter RUNS=3;
 localparam MID=1 << (BITS-1);
 reg clk=0,rst=1,arm=0,disarm=0,abort_run=0,soft_pulse=0,ext=0;
 always #2.5 clk=~clk;
 reg [15:0] repeats=0;
 wire read_en,prepared;wire [12:0] address,index;
 reg [BITS+31:0] descriptor;
 wire [1:0] state;wire [BITS-1:0] code;
 wire [15:0] loops;wire [31:0] ignored;
 integer i,finite_case;
 sample_engine #(.DAC_BITS(BITS)) dut(clk,rst,arm,disarm,abort_run,soft_pulse,ext,
  1'b1,EXTERNAL!=0,BITS'(MID),BITS'(MID+2),repeats,14'(RUNS),
  read_en,address,descriptor,state,prepared,code,index,loops,ignored);
 always @(posedge clk) if(read_en) descriptor<={BITS'(MID-1-address),32'd1};
 task tick;begin @(posedge clk);#0.1;end endtask
 task prepare;begin
  @(negedge clk);arm=1;tick;@(negedge clk);arm=0;
  wait(prepared);#0.1;if(code!=MID)$fatal(1,"arm initial");
 end endtask
 task trigger;begin
  @(negedge clk);if(EXTERNAL)ext=1;else soft_pulse=1;tick;
  @(negedge clk);ext=0;soft_pulse=0;
 end endtask
 initial begin
  repeat(5)tick;@(negedge clk);rst=0;
  prepare;trigger;
  if(state!=2 || code!=MID-1)$fatal(1,"first sample");
  // Cross both saturation and wrap of a 16-bit whole-table counter. Every
  // descriptor is one sample long, including the last -> first boundary.
  for(i=1;i<RUNS*65538;i=i+1)begin
   tick;if(state!=2 || code!=MID-1-(i%RUNS))$fatal(1,"infinite sample %d code %d state %d",i,code,state);
  end
  if(loops!=65535)$fatal(1,"loop display must saturate");
  trigger;if(state!=2 || ignored!=1)$fatal(1,"busy trigger must not restart");
  @(negedge clk);abort_run=1;tick;@(negedge clk);abort_run=0;
  if(state!=0 || code!=MID+2)$fatal(1,"abort safe code");
  trigger;repeat(8)tick;if(state!=0 || code!=MID+2)$fatal(1,"abort must not rearm");
  prepare;@(negedge clk);disarm=1;tick;@(negedge clk);disarm=0;
  if(state!=0 || code!=MID)$fatal(1,"disarm initial");
  // Largest finite setting remains finite after using the infinite setting.
  for(finite_case=0;finite_case<3;finite_case=finite_case+1)begin
   repeats=finite_case==0 ? 65535:finite_case;prepare;trigger;
   for(i=1;i<RUNS*repeats;i=i+1)begin
    tick;if(state!=2 || code!=MID-1-(i%RUNS))$fatal(1,"finite sample %d",i);
   end
   tick;if(state!=(EXTERNAL ? 1:3) || code!=MID || loops!=repeats)$fatal(1,"finite end/rearm");
  end
  $display("PASS infinite BITS=%d EXTERNAL=%d RUNS=%d, every clock, 65538 wraps, abort and finite 1/2/65535",BITS,EXTERNAL,RUNS);$finish;
 end
 initial begin #3000000;$fatal(1,"timeout");end
endmodule
