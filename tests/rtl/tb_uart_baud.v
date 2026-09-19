`timescale 1ns/1ps
// Independent UART peer: bit cells use the ideal baud rate, not the DUT divider.
module tb_uart_baud;
 parameter BAUD=1000000, CORE_HZ=200000000;
 localparam real BIT_NS=1000000000.0/BAUD;
 reg clk=0; always #(500000000.0/CORE_HZ) clk=~clk;
 reg rst=1,rx=1;
 wire tx,request_valid,response_ready;
 wire [255:0] request;
 reg [239:0] response=0;
 reg response_valid=0;
 reg [255:0] requests[0:1],responses[0:1],received;
 integer accepted=0,i,j,exchanges;
 reg exact_width_checked=0;
 realtime first_edge;
 packet_link #(.CLK_HZ(CORE_HZ),.BAUD(BAUD)) dut(
  clk,rst,rx,tx,request,request_valid,response,response_valid,response_ready);

 // A small independent responder validates the exact received frame before
 // supplying the 30-byte response. packet_link must generate its wire CRC.
 always @(posedge clk) begin
  response_valid<=0;
  if(!rst && request_valid) begin
   if(accepted>=2 || request!==requests[accepted]) $fatal(1,"request bytes/CRC mismatch");
   if(!response_ready) $fatal(1,"response unexpectedly busy");
   response<=responses[accepted][239:0]; response_valid<=1;
   accepted=accepted+1;
  end
 end

 task send_byte; input [7:0] value; integer bit_no;
  begin
   rx=0; #(BIT_NS);
   for(bit_no=0;bit_no<8;bit_no=bit_no+1) begin rx=value[bit_no]; #(BIT_NS); end
   rx=1; #(BIT_NS);
  end
 endtask
 task send_frame; input [255:0] frame; integer byte_no;
  begin for(byte_no=0;byte_no<32;byte_no=byte_no+1) send_byte(frame[byte_no*8+:8]); end
 endtask
 task receive_frame; integer byte_no,bit_no;
  begin
   received=0;
   for(byte_no=0;byte_no<32;byte_no=byte_no+1) begin
    @(negedge tx); #(BIT_NS/2);
    if(tx!==0) $fatal(1,"invalid start bit");
    for(bit_no=0;bit_no<8;bit_no=bit_no+1) begin
     #(BIT_NS); received[byte_no*8+bit_no]=tx;
    end
    #(BIT_NS); if(tx!==1) $fatal(1,"invalid stop bit");
   end
  end
 endtask

 initial begin
  if(BAUD==1000000) begin
   wait(!rst); @(negedge tx); first_edge=$realtime;
   @(posedge tx);
   if($realtime-first_edge!=1000.0) $fatal(1,"1 Mbaud bit width is not exactly 1 us");
   exact_width_checked=1;
  end
 end
 initial begin
  $readmemh("requests.hex",requests); $readmemh("responses.hex",responses);
  repeat(5) @(negedge clk); rst=0; #(BIT_NS*2);
  if(BAUD==1000000) begin
   if(dut.u_rx.DIV!=CORE_HZ/BAUD || dut.u_tx.DIV!=CORE_HZ/BAUD) $fatal(1,"1 Mbaud divider does not match sample clock");
   send_frame(requests[0] ^ (256'd1<<240)); // Deliberately corrupt only the CRC.
   #(BIT_NS*2);
   if(accepted!=0 || tx!==1 || !response_ready) $fatal(1,"bad CRC was not ignored");
  end
  exchanges=BAUD==1000000 ? 2:1;
  for(i=0;i<exchanges;i=i+1) begin
   fork
    send_frame(requests[i]);
    receive_frame;
   join
   if(received!==responses[i]) $fatal(1,"response bytes/CRC mismatch: %064h",received);
   #(BIT_NS*2);
  end
  if(accepted!=exchanges) $fatal(1,"wrong number of accepted requests");
  if(BAUD==1000000 && !exact_width_checked) $fatal(1,"1 Mbaud bit width was not observed");
  $display("PASS baud=%0d: nominal peer TX/RX, full frame CRC, divider=%0d",BAUD,dut.u_tx.DIV);
  $finish;
 end
 initial begin #(BIT_NS*2000); $fatal(1,"UART baud test timeout"); end
endmodule
