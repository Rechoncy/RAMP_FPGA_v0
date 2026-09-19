`timescale 1ns/1ps
module tb_dual_transport;
 reg clk=0,usb_clk=0,rst=0,rx=1,usb_clock_enabled=0;
 always #2.5 clk=~clk;always #8.333 if(usb_clock_enabled)usb_clk=~usb_clk;
 wire tx;tri [7:0] data;reg rxf_n=1,txe_n=1;reg [7:0] input_data=0;
 wire oe_n,rd_n,wr_n,siwu_n;
 wire [255:0] uart_request,usb_request,request;
 wire [239:0] uart_response,usb_response,response;
 wire uart_valid,uart_reply_valid,uart_ready,usb_valid,usb_req_ready,usb_reply_valid,usb_ready;
 wire request_valid,response_valid,response_ready;
 reg [1:0] rise=0;wire [39:0] codes;
 packet_link #(.CLK_HZ(2000000),.BAUD(100000)) uart_link(
  clk,rst,rx,tx,uart_request,uart_valid,uart_response,uart_reply_valid,uart_ready);
 usb_packet_link #(.FIFO_ADDR_BITS(4)) usb_link(
  clk,rst,usb_clk,rst,data,rxf_n,txe_n,oe_n,rd_n,wr_n,siwu_n,
  usb_request,usb_valid,usb_req_ready,usb_response,usb_reply_valid,usb_ready);
 dual_packet_router router(clk,rst,
  uart_request,uart_valid,uart_response,uart_reply_valid,uart_ready,
  usb_request,usb_valid,usb_req_ready,usb_response,usb_reply_valid,usb_ready,
  request,request_valid,response,response_valid,response_ready);
 parallel_controller #(.RUN_ADDR_BITS(6),.USB_FIFO_ENABLED(1),.SOURCE_TAGGED(1)) control(
  clk,rst,request,request_valid,response,response_valid,response_ready,rise,2'b0,codes);
 reg [255:0] usb_frames[0:10],uart_frames[0:5],usb_reply=0,uart_reply=0;
 integer sent=0,received=0,usb_limit=0,cycle=0,usb_file,uart_file,stalls=0,status_exec=0;
 assign data=!oe_n && !rxf_n ? input_data:8'bz;
 always @(posedge clk)if(!rst && control.state==3 && control.incoming[31:24]==2)status_exec=status_exec+1;
 always @(posedge usb_clk)if(!rst)begin
  cycle=cycle+1;
  if(!rd_n && !rxf_n)sent=sent+1;
  if(!wr_n && !txe_n)begin
   if(!oe_n || !rd_n)$fatal(1,"USB contention");
   usb_reply[(received%32)*8+:8]=data;received=received+1;
   if(received%32==0)$fdisplay(usb_file,"%064h",usb_reply);
  end
  if(!rxf_n && rd_n)stalls=stalls+1;
  #9;
  rxf_n=sent>=usb_limit || cycle%17<4;
  txe_n=cycle%97<43;
  if(sent<usb_limit)input_data=usb_frames[sent/32][(sent%32)*8+:8];
 end
 task send_byte(input [7:0] value);integer bit_index;
 begin
  @(negedge clk);rx=0;repeat(20)@(negedge clk);
  for(bit_index=0;bit_index<8;bit_index=bit_index+1)begin rx=value[bit_index];repeat(20)@(negedge clk);end
  rx=1;repeat(20)@(negedge clk);
 end endtask
 task receive_uart;integer byte_index,bit_index;
 begin
  uart_reply=0;
  for(byte_index=0;byte_index<32;byte_index=byte_index+1)begin
   @(negedge tx);repeat(30)@(negedge clk);
   for(bit_index=0;bit_index<8;bit_index=bit_index+1)begin
    uart_reply[byte_index*8+bit_index]=tx;repeat(20)@(negedge clk);
   end
   if(!tx)$fatal(1,"bad UART stop bit");
  end
  $fdisplay(uart_file,"%064h",uart_reply);
 end endtask
 task uart_exchange(input integer index);integer byte_index;
 begin
  fork
   begin for(byte_index=0;byte_index<32;byte_index=byte_index+1)send_byte(uart_frames[index][byte_index*8+:8]);end
   receive_uart;
  join
  repeat(25)@(negedge clk);
 end endtask
 task usb_exchange(input integer index);
 begin usb_limit=(index+1)*32;wait(received==(index+1)*32);repeat(10)@(negedge clk);end
 endtask
 integer n;
 initial begin
  $readmemh("usb_requests.hex",usb_frames);$readmemh("uart_requests.hex",uart_frames);
  usb_file=$fopen("usb_responses.hex","w");uart_file=$fopen("uart_responses.hex","w");
  // An explicit asynchronous assertion also resets the stopped USB domain.
  #1;rst=1;#70;rst=0;
  uart_exchange(0); // serial works before FT232H starts its 60 MHz CLKOUT
  usb_clock_enabled=1;
  for(n=0;n<6;n=n+1)usb_exchange(n);
  uart_exchange(1); // same STATUS wire packet is next sent through USB
  @(negedge clk);rise=1;@(negedge clk);rise=0;
  usb_exchange(6);
  #220000;
  usb_exchange(7); // same-source retry must return the earlier reply verbatim
  uart_exchange(2); // same bytes, different source: execute and read live code
  if(status_exec!=3)$fatal(1,"source-tagged retry cache: executed %0d, expected 3",status_exec);
  fork
   uart_exchange(3);
   begin repeat(6100)@(negedge clk);usb_exchange(8);end
  join
  uart_exchange(4); // abort from UART after USB upload, no reboot/reconfigure
  usb_exchange(9);usb_exchange(10);
  if(status_exec!=4)$fatal(1,"last same-source STATUS retry executed twice");
  if(codes[9:0]!==77 || codes[19:10]!==512 || codes[29:20]!==512 || codes[39:30]!==512)
   $fatal(1,"dual port control/channel isolation failed");
  if(!stalls || received!=352)$fatal(1,"USB coverage incomplete");
  @(negedge usb_clk);usb_clock_enabled=0;
  uart_exchange(5); // serial remains available after a settled USB disconnect
  $fclose(usb_file);$fclose(uart_file);
  $display("PASS dual transport: real UART/USB, interface switch, tagged retry cache and routed replies");$finish;
 end
 initial begin #1500000;$fatal(1,"dual transport timeout usb=%0d router=%0d uart_valid=%b usb_valid=%b usb_reset=%b",received,router.state,uart_valid,usb_valid,usb_link.usb_reset);end
endmodule
