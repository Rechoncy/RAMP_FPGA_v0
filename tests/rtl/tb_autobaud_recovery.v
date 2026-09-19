`timescale 1ns/1ps
module tb_autobaud_recovery;
 // Reduced clock parameter makes the 250 ms timeout affordable in simulation;
 // the separate wire tests exercise all nine rates at actual 100/125/200 MHz.
 localparam HZ=2000000, DEFAULT_DIV=(HZ+57600)/115200;
 reg clk=0;always #5 clk=~clk;
 reg rst=1,rx=1,idle=1;wire [15:0] divider;wire pause_rx;
 integer i;
 uart_autobaud #(.CLK_HZ(HZ)) dut(clk,rst,rx,idle,divider,pause_rx);
 task ticks;input integer n;begin repeat(n)@(negedge clk);end endtask
 task break_line;begin rx=0;ticks(HZ/100+20);rx=1;ticks(20);end endtask
 task toggles;input integer div,n;integer k;
 begin for(k=0;k<n;k=k+1)begin rx=~rx;ticks(div);end rx=1;end endtask
 task recover;
 begin ticks(HZ/4+20);if(pause_rx || divider!==DEFAULT_DIV)$fatal(1,"failed timeout recovery");end endtask
 initial begin
  ticks(5);rst=0;ticks(5);
  toggles(208,100);ticks(3000);
  if(divider!==DEFAULT_DIV || pause_rx)$fatal(1,"payload changed baud without BREAK");
  rx=0;ticks(HZ/100-100);rx=1;ticks(30);toggles(208,100);ticks(3000);
  if(divider!==DEFAULT_DIV || pause_rx)$fatal(1,"short BREAK accepted");
  break_line;toggles(208,12);recover; // fewer than 24 valid intervals
  break_line;toggles(75,70);recover; // unsupported frequency
  break_line;
  for(i=0;i<8;i=i+1)begin toggles(208,8);ticks(208*3);end
  recover; // incomplete byte bursts separated by high gaps cannot accumulate
  break_line;
  for(i=0;i<8;i=i+1)begin toggles(208,12);toggles(75,4);end
  recover; // separated valid bursts must not be added together
  idle=0;break_line;toggles(208,160);ticks(4000);
  if(divider!==DEFAULT_DIV || !pause_rx)$fatal(1,"changed divider with pending TX");
  idle=1;ticks(10);
  if(divider!==208 || pause_rx)$fatal(1,"did not commit after TX drained");
  // Repeated BREAK reacquires even after an incomplete earlier training.
  break_line;toggles(104,12);break_line;toggles(104,160);ticks(2000);
  if(divider!==104 || pause_rx)$fatal(1,"retraining failed");
  rst=1;ticks(5);rst=0;ticks(5);
  if(divider!==DEFAULT_DIV)$fatal(1,"reset did not restore power-on baud");
  $display("PASS autobaud recovery: no BREAK, short BREAK, incomplete, unsupported, noise, TX drain, reset");$finish;
 end
 initial begin #30000000;$fatal(1,"timeout");end
endmodule
