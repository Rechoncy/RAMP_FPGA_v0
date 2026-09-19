`timescale 1ns/1ps
// Fixed 32-byte frame: A5 5A, version, command, sequence LE, payload[24], CRC16 LE.
module packet_link #(parameter CLK_HZ=200000000, BAUD=115200, AUTOBAUD=1)(
 input clk,rst,rx,output tx,
 output reg [255:0] request,output reg request_valid,
 input [239:0] response,input response_valid,output response_ready);
 wire [7:0] rx_data; wire rx_valid,tx_ready;
 reg [255:0] rx_frame,tx_frame;
 reg [5:0] rx_index,tx_index;
 reg [15:0] rx_crc,tx_crc;
 reg [23:0] timeout_count;
 reg [1:0] tx_state;
 reg [5:0] crc_index;
 function [15:0] crc_byte;
  input [15:0] value; input [7:0] b; integer j; reg [15:0] c;
  begin c=value ^ {b,8'h00}; for(j=0;j<8;j=j+1) c=c[15] ? (c<<1)^16'h1021:c<<1; crc_byte=c; end
 endfunction
 wire [7:0] fixed_rx_data;wire fixed_rx_valid,fixed_tx_ready,fixed_tx,pause_rx;
 // Unused fixed UARTs are removed by synthesis in the automatic branch.
 uart_rx #(.CLK_HZ(CLK_HZ),.BAUD(BAUD)) u_rx(clk,rst,rx,fixed_rx_data,fixed_rx_valid);
 uart_tx #(.CLK_HZ(CLK_HZ),.BAUD(BAUD)) u_tx(clk,rst,tx_frame[7:0],!AUTOBAUD && tx_state==2,fixed_tx_ready,fixed_tx);
 generate if(AUTOBAUD)begin: automatic_baud
  wire [15:0] divider;
  uart_autobaud #(.CLK_HZ(CLK_HZ),.BAUD(BAUD)) detector(
   clk,rst,rx,tx_state==0 && tx_ready && !response_valid && !request_valid,divider,pause_rx);
  uart_rx_dynamic receiver(clk,rst || pause_rx,rx,divider,rx_data,rx_valid);
  uart_tx_dynamic transmitter(clk,rst,divider,tx_frame[7:0],tx_state==2,tx_ready,tx);
 end else begin: fixed_baud
  assign rx_data=fixed_rx_data;assign rx_valid=fixed_rx_valid;
  assign tx_ready=fixed_tx_ready;assign tx=fixed_tx;assign pause_rx=0;
 end endgenerate
 assign response_ready=tx_state==0;
 always @(posedge clk) begin
  if(rst) begin rx_index<=0; rx_crc<=16'hffff; timeout_count<=0; rx_frame<=0; request<=0; request_valid<=0;
   tx_state<=0; tx_index<=0; tx_frame<=0; tx_crc<=16'hffff; crc_index<=0; end
  else begin
   request_valid<=0;
   if(pause_rx)begin rx_index<=0;rx_crc<=16'hffff;timeout_count<=0;end
   else begin
   if(rx_index!=0) begin
    if(timeout_count==CLK_HZ/100-1) begin rx_index<=0; timeout_count<=0; end
    else timeout_count<=timeout_count+1'b1;
   end
   if(rx_valid) begin
    timeout_count<=0;
    if(rx_index==0) begin if(rx_data==8'ha5) begin rx_frame[7:0]<=rx_data; rx_index<=1; end end
    else if(rx_index==1) begin
     if(rx_data==8'h5a) begin rx_frame[15:8]<=rx_data; rx_index<=2; rx_crc<=16'hffff; end
     else if(rx_data!=8'ha5) rx_index<=0;
    end else begin
     rx_frame[rx_index*8+:8]<=rx_data;
     if(rx_index<30) rx_crc<=crc_byte(rx_crc,rx_data);
     if(rx_index==31) begin
      rx_index<=0;
      if({rx_data,rx_frame[247:240]}==rx_crc) begin request<={rx_data,rx_frame[247:0]}; request_valid<=1; end
     end else rx_index<=rx_index+1'b1;
    end
   end
   end
   case(tx_state)
    0: if(response_valid) begin tx_frame<={16'h0,response}; tx_crc<=16'hffff; crc_index<=2; tx_state<=1; end
    1: begin
     tx_crc<=crc_byte(tx_crc,tx_frame[crc_index*8+:8]);
     if(crc_index==29) begin tx_frame[255:240]<=crc_byte(tx_crc,tx_frame[crc_index*8+:8]); tx_index<=0; tx_state<=2; end
     else crc_index<=crc_index+1'b1;
    end
    2: if(tx_ready) begin tx_frame<={8'hff,tx_frame[255:8]};
     if(tx_index==31) tx_state<=3; else tx_index<=tx_index+1'b1; end
    3: if(tx_ready) tx_state<=0;
   endcase
  end
 end
endmodule
