`timescale 1ns/1ps
// Exercise full/empty, repeated wrap and shared asynchronous reset while full.
// Drive on falling edges; the scoreboard compares only accepted transactions.
module tb_usb_async_fifo;
 reg wr_clk=0,rd_clk=0,rst=1,wr_valid=0,rd_ready=0;
 always #3.5 wr_clk=~wr_clk;
 always #5.5 rd_clk=~rd_clk;
 reg [7:0] wr_data=0;
 wire [7:0] rd_data;
 wire wr_ready,rd_valid;
 integer produced=0,consumed=0,phase=0,wr_cycle=0,rd_cycle=0;
 integer full_cycles=0,empty_cycles=0;
 usb_async_fifo #(.ADDR_BITS(3)) dut(
  wr_clk,rst,wr_data,wr_valid,wr_ready,rd_clk,rst,rd_data,rd_valid,rd_ready);
 function [7:0] value;
  input integer index;
  begin value=(index*37+phase*91)&255;end
 endfunction
 always @(negedge wr_clk)begin
  wr_cycle=wr_cycle+1;
  wr_valid=!rst && produced<1000 && wr_cycle%11>1;
  wr_data=value(produced);
 end
 always @(negedge rd_clk)begin
  rd_cycle=rd_cycle+1;
  rd_ready=!rst && phase!=0 && rd_cycle%17>6;
 end
 always @(posedge wr_clk)if(!rst)begin
  if(wr_valid && wr_ready)produced=produced+1;
  if(!wr_ready)full_cycles=full_cycles+1;
 end
 always @(posedge rd_clk)if(!rst)begin
  if(rd_valid && rd_ready)begin
   if(consumed>=produced || rd_data!==value(consumed))
    $fatal(1,"FIFO ordering after wrap/reset: phase=%0d index=%0d got=%h",phase,consumed,rd_data);
   consumed=consumed+1;
  end
  if(!rd_valid)empty_cycles=empty_cycles+1;
 end
 initial begin
  #43;rst=0;
  wait(produced==8);#91;
  if(wr_ready || rd_data!==value(0))$fatal(1,"FIFO did not preserve full contents");
  // Reset discards pending data; release before either clock's next active edge.
  #1;rst=1;#47;produced=0;consumed=0;phase=1;rst=0;
  wait(consumed==1000);repeat(10)@(posedge rd_clk);
  if(rd_valid || produced!=consumed || full_cycles==0 || empty_cycles==0)
   $fatal(1,"FIFO coverage/full/empty failure");
  $display("PASS FIFO: 1000 bytes, pointer wraps, stalls and reset while full");$finish;
 end
 initial begin #1000000;$fatal(1,"FIFO timed out");end
endmodule
