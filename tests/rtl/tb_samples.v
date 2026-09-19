`timescale 1ns/1ps
module tb_samples;
 reg clk=0; always #2.5 clk=~clk;
 reg rst=1,arm=0,disarm=0,abort_run=0,trigger=0;
 reg [13:0] count;
 reg [15:0] repeats;
 reg [41:0] memory[0:8191],descriptor;
 wire read_en,prepared;
 wire [12:0] address,index;
 wire [1:0] state;
 wire [9:0] code;
 wire [15:0] loops;
 wire [31:0] ignored;
 integer c,i,j,k,n,loop_no;
 sample_engine dut(clk,rst,arm,disarm,abort_run,trigger,1'b0,1'b1,1'b0,
  10'd512,10'd512,repeats,count,read_en,address,descriptor,state,prepared,code,index,loops,ignored);
 always @(posedge clk) if(read_en) descriptor<=memory[address];
 task prepare;
  begin
   @(negedge clk); arm=1; @(negedge clk); arm=0;
   wait(prepared); @(negedge clk); trigger=1; @(negedge clk); trigger=0;
  end
 endtask
 initial begin
  repeat(4) @(negedge clk); rst=0;
  for(c=0;c<5;c=c+1) begin
   case(c) 0:count=1; 1:count=2; 2:count=4; 3:count=8192; 4:count=71; endcase
   repeats=3;
   for(i=0;i<count;i=i+1) begin
    n=(c==4) ? ((i*17)%31+1):1;
    memory[i]=(((i*391+13)%1024)*64'd4294967296)+n;
   end
   prepare;
   for(loop_no=0;loop_no<repeats;loop_no=loop_no+1)
    for(i=0;i<count;i=i+1)
     for(j=0;j<memory[i][31:0];j=j+1) begin
      if(state!==2 || code!==memory[i][41:32])
       $fatal(1,"sample mismatch case=%0d loop=%0d run=%0d tick=%0d code=%0d expected=%0d",c,loop_no,i,j,code,memory[i][41:32]);
      @(negedge clk);
     end
   if(state!==3 || code!==512 || loops!==repeats) $fatal(1,"end timing or zero return mismatch");
   // Re-arm after prefetching the old final bank, abort, then start cleanly.
   prepare; abort_run=1; @(negedge clk); abort_run=0;
   if(state!==0 || code!==512) $fatal(1,"abort mismatch");
  end
  $display("PASS samples: every cycle, 8192 one-clock runs, three repeats, variable lengths, abort/rearm"); $finish;
 end
 initial begin #1000000; $fatal(1,"timeout"); end
endmodule
