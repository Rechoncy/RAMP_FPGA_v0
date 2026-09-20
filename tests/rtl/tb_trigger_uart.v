`timescale 1ns/1ps
module tb_trigger_uart;
 parameter COUNT=32;
 reg clk=0; always #2.5 clk=~clk;
 reg rst=1,rx=1;
 wire tx;
 wire [255:0] request;
 wire request_valid;
 wire [239:0] response;
 wire response_valid,response_ready;
 wire [39:0] codes;
 reg [255:0] vectors[0:COUNT-1];
 reg [255:0] received;
 integer i,j,k,output_file;
 packet_link #(.CLK_HZ(800000),.BAUD(100000),.AUTOBAUD(0)) link(clk,rst,rx,tx,request,request_valid,response,response_valid,response_ready);
 parallel_controller #(.RUN_ADDR_BITS(6)) control(clk,rst,request,request_valid,response,response_valid,response_ready,2'b0,2'b0,codes);
 task send_byte; input [7:0] b; integer n;
  begin
   rx=0; repeat(8) @(negedge clk);
   for(n=0;n<8;n=n+1) begin rx=b[n]; repeat(8) @(negedge clk); end
   rx=1; repeat(8) @(negedge clk);
  end
 endtask
 task receive_frame; integer byte_no,bit_no;
  begin
   received=0;
   for(byte_no=0;byte_no<32;byte_no=byte_no+1) begin
    @(negedge tx); repeat(12) @(negedge clk);
    for(bit_no=0;bit_no<8;bit_no=bit_no+1) begin
     received[byte_no*8+bit_no]=tx;
     repeat(8) @(negedge clk);
    end
    if(!tx) $fatal(1,"bad UART stop bit");
   end
  end
 endtask
 initial begin
  $readmemh("parallel_requests.hex",vectors);
  output_file=$fopen("parallel_responses.hex","w");
  repeat(5) @(negedge clk); rst=0; repeat(8) @(negedge clk);
  for(i=0;i<COUNT;i=i+1) begin
   fork
    begin for(j=0;j<32;j=j+1) send_byte(vectors[i][j*8+:8]); end
    receive_frame;
   join
   $fdisplay(output_file,"%064h",received);
   repeat(50) @(negedge clk);
  end
  $fclose(output_file); if(codes!={4{10'd512}}) $fatal(1,"abort"); $display("PASS UART virtual trigger protocol exchange"); $finish;
 end
 initial begin #20000000; $fatal(1,"protocol timeout"); end
endmodule
