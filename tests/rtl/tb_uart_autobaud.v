`timescale 1ns/1ps
module tb_uart_autobaud;
 parameter CORE_HZ=125000000, RATE_COUNT=9, AUTOBAUD=1, BYTE_GAP_BITS=0;
 reg clk=0;always #(500000000.0/CORE_HZ)clk=~clk;
 reg rst=1,rx=1;
 wire tx,request_valid,response_ready;wire [255:0] request;
 reg response_valid=0;reg [239:0] response=0;
 reg [255:0] requests[0:9],responses[0:9],received;
 integer accepted=0,i,j,k,baud,expected_count;
 real bit_ns=1000000000.0/115200,send_ns=1000000000.0/115200;
 reg [47:0] free_running=0;reg [47:0] before_training;
 packet_link #(.CLK_HZ(CORE_HZ),.BAUD(115200),.AUTOBAUD(AUTOBAUD)) dut(
  clk,rst,rx,tx,request,request_valid,response,response_valid,response_ready);
 always @(posedge clk)begin
  if(!rst)free_running<=free_running+1'b1;
  response_valid<=0;
  if(request_valid)begin
   if(request!==requests[accepted])$fatal(1,"unexpected command during training/noise or corrupt request %d",accepted);
   if(!response_ready)$fatal(1,"reply overlap");
   response<=responses[accepted][239:0];response_valid<=1;accepted=accepted+1;
  end
 end
 task send_byte;input [7:0] value;integer b;
 begin rx=0;#(send_ns);for(b=0;b<8;b=b+1)begin rx=value[b];#(send_ns);end rx=1;#(send_ns*(1+BYTE_GAP_BITS));end endtask
 task send_frame;input [255:0] value;integer b;
 begin for(b=0;b<32;b=b+1)send_byte(value[b*8+:8]);end endtask
 task receive_frame;integer b,n;
 begin
  received=0;
  for(b=0;b<32;b=b+1)begin
   @(negedge tx);#(bit_ns/2);if(tx!==0)$fatal(1,"start");
   for(n=0;n<8;n=n+1)begin #(bit_ns);received[b*8+n]=tx;end
   #(bit_ns);if(tx!==1)$fatal(1,"stop");
  end
 end endtask
 task exchange;input integer n;
 begin
  fork send_frame(requests[n]);receive_frame;join
  if(received!==responses[n])$fatal(1,"response CRC/bytes baud=%d",baud);
  #(bit_ns*4);
 end endtask
 task train;
 begin
  expected_count=accepted;before_training=free_running;
  // Real production BREAK threshold, not a shortened simulation constant.
  rx=0;#10020000;rx=1;#10000;
  for(j=0;j<16;j=j+1)send_byte(8'h55);
  rx=1;#(bit_ns*20+10000);
  if(accepted!=expected_count)$fatal(1,"training executed a command");
  if(free_running<=before_training)$fatal(1,"core clock stopped");
 end endtask
 generate if(AUTOBAUD)begin
  always @(dut.automatic_baud.divider)begin
   if(!rst && !dut.response_ready)$fatal(1,"divider changed during TX");
  end
 end endgenerate
 initial begin
  $readmemh("requests.hex",requests);$readmemh("responses.hex",responses);
  repeat(8)@(negedge clk);rst=0;#1000;baud=115200;
  exchange(0); // Original fixed-default hosts still work without training.
  for(i=0;i<RATE_COUNT;i=i+1)begin
   if(AUTOBAUD)case(i)
    0:baud=1000000;1:baud=9600;2:baud=921600;3:baud=19200;4:baud=460800;
    5:baud=38400;6:baud=230400;7:baud=57600;default:baud=115200;
   endcase
   bit_ns=1000000000.0/baud;send_ns=bit_ns*(i%2 ? 0.992:1.008);
   train;
   send_frame(requests[i+1] ^ (256'd1<<240));#(bit_ns*4);
   if(accepted!=i+1)$fatal(1,"bad CRC reached controller");
   exchange(i+1);
  end
  if(accepted!=RATE_COUNT+1)$fatal(1,"missing requests");
  $display("PASS auto baud core=%d rates=%d auto=%d: same instance, +/-0.8%% peer, full CRC, default compatibility",CORE_HZ,RATE_COUNT,AUTOBAUD);
  $finish;
 end
 initial begin #1500000000;$fatal(1,"autobaud timeout");end
endmodule
