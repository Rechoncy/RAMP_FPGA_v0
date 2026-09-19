`timescale 1ns/1ps
module tb_protocol;
 reg clk=0; always #2.5 clk=~clk;
 reg rst=1,rx=1;
 wire tx;
 wire [255:0] request;
 wire request_valid;
 wire [239:0] response;
 wire response_valid,response_ready;
 wire [9:0] code,fm;
 reg [255:0] vectors[0:31];
 reg [255:0] received;
 integer i,j,k,output_file;
 packet_link #(.CLK_HZ(2000000),.BAUD(100000)) link(clk,rst,rx,tx,request,request_valid,response,response_valid,response_ready);
 ramp_controller control(clk,rst,request,request_valid,response,response_valid,response_ready,2'b0,2'b0,{fm,code},);
 task send_byte; input [7:0] b; integer n;
  begin
   rx=0; repeat(20) @(negedge clk);
   for(n=0;n<8;n=n+1) begin rx=b[n]; repeat(20) @(negedge clk); end
   rx=1; repeat(20) @(negedge clk);
  end
 endtask
 task receive_frame; integer byte_no,bit_no;
  begin
   received=0;
   for(byte_no=0;byte_no<32;byte_no=byte_no+1) begin
    @(negedge tx); repeat(30) @(negedge clk);
    for(bit_no=0;bit_no<8;bit_no=bit_no+1) begin
     received[byte_no*8+bit_no]=tx;
     repeat(20) @(negedge clk);
    end
    if(!tx) $fatal(1,"bad UART stop bit");
   end
  end
 endtask
 initial begin
  $readmemh("build/protocol_vectors.hex",vectors);
  output_file=$fopen("build/protocol_responses.hex","w");
  repeat(5) @(negedge clk); rst=0; repeat(20) @(negedge clk);
  for(i=0;i<18;i=i+1) begin
   fork
    begin for(j=0;j<32;j=j+1) send_byte(vectors[i][j*8+:8]); end
    receive_frame;
   join
   $fdisplay(output_file,"%064h",received);
   repeat(50) @(negedge clk);
  end
  $fclose(output_file); $display("PASS UART protocol exchange: 18 frames"); $finish;
 end
 initial begin #3000000; $fatal(1,"protocol timeout"); end
endmodule
