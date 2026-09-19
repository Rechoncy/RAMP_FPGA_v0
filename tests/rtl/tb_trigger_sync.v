`timescale 1ns/1ps
module tb_trigger_sync;
 reg clk=0; always #2.5 clk=~clk;
 reg rst=1,level=0;
 wire rising,falling;
 integer nr=0,nf=0,i;
 trigger_sync dut(clk,rst,level,rising,falling);
 always @(negedge clk) begin if(rising) nr=nr+1; if(falling) nf=nf+1; end
 initial begin
  for(i=0;i<2;i=i+1) begin
   rst=1; level=i; repeat(5) @(negedge clk); nr=0; nf=0; rst=0;
   repeat(12) @(negedge clk);
   if(nr!=0 || nf!=0) $fatal(1,"phantom startup edge");
   #1; level=!i; repeat(12) @(negedge clk);
   if(nr!=(i==0) || nf!=(i==1)) $fatal(1,"missing/wide synchronized edge");
   #1; level=i; repeat(12) @(negedge clk);
   if(nr!=1 || nf!=1) $fatal(1,"reverse edge mismatch");
  end
  $display("PASS trigger synchronizer: both edges, initial high/low suppression, one-clock pulses"); $finish;
 end
endmodule
