`timescale 1ns/1ps
module tb_random;
 reg clk=0; always #2.5 clk=~clk;
 reg rst=1,arm=0,disarm=0,abort_run=0,soft_trigger=0;
 reg [9:0] initial_code=100;
 reg [15:0] divider=16,repeats=1;
 reg [6:0] count;
 wire [5:0] read_index,index; reg [83:0] desc;
 wire [1:0] state; wire prepared; wire [9:0] code;
 wire [15:0] loops; wire [31:0] ignored;
 reg [83:0] memory[0:63];
 reg [83:0] vectors[0:767];
 reg [31:0] parameters[0:47];
 integer testno,i,k,j,cycle,old_value,target,n,d,expected,total,done_steps;
 sequence_engine dut(clk,rst,arm,disarm,abort_run,soft_trigger,1'b0,
  1'b1,1'b0,initial_code,10'd0,divider,repeats,count,read_index,desc,state,prepared,code,index,loops,ignored);
 always @(posedge clk) desc<=memory[read_index];
 initial begin
  $readmemh("build/random_descriptors.hex",vectors);
  $readmemh("build/random_parameters.hex",parameters);
  for(testno=0;testno<12;testno=testno+1) begin
   rst=1; repeat(4) @(negedge clk);
   initial_code=parameters[testno*4]; count=parameters[testno*4+1]; repeats=parameters[testno*4+2]; divider=parameters[testno*4+3];
   for(i=0;i<64;i=i+1) memory[i]=vectors[testno*64+i];
   rst=0; @(negedge clk); arm=1; @(negedge clk); arm=0;
   wait(prepared); @(negedge clk); soft_trigger=1; @(negedge clk); soft_trigger=0;
   if(state!=2) $fatal(1,"not started");
   old_value=initial_code;
   for(j=0;j<repeats;j=j+1) begin
    for(i=0;i<count;i=i+1) begin
     target=memory[i][83:74]; n=memory[i][31:0]; d=target-old_value;
     for(k=1;k<=n;k=k+1) begin
      repeat(divider) @(negedge clk);
      expected=old_value+(d>=0 ? (d*k/n): -((-d)*k/n));
      if(code!=expected) $fatal(1,"test %0d loop %0d segment %0d sample %0d got %0d expected %0d",testno,j,i,k,code,expected);
     end
     old_value=target;
    end
   end
   if(state!=3 || loops!=repeats) $fatal(1,"bad final state test %0d",testno);
  end
  $display("PASS random: 12 profiles, exact every-sample codes, 1..64 segments, short segments, repeats"); $finish;
 end
 initial begin #10000000; $fatal(1,"random timeout"); end
endmodule
