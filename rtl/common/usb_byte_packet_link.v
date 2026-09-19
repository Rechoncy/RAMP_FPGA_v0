`timescale 1ns/1ps
// Backpressured 32-byte CRC packet interface. A request is held until accepted;
// the next packet stays in the RX FIFO until this reply has been transmitted.
// Thus a fast host cannot overwrite the controller's single pending command.
module usb_byte_packet_link #(parameter TIMEOUT_CYCLES=2000000)(
 input clk,rst,input [7:0] rx_data,input rx_valid,output rx_ready,
 output [7:0] tx_data,output tx_valid,input tx_ready,
 output reg [255:0] request,output reg request_valid,input request_ready,
 input [239:0] response,input response_valid,output response_ready);
 reg [255:0] rx_frame,tx_frame;
 reg [5:0] rx_index,tx_index,crc_index;
 reg [15:0] rx_crc,tx_crc;
 reg [31:0] timeout_count;
 reg [1:0] tx_state;
 reg waiting_reply;
 function [15:0] crc_byte;
  input [15:0] value;input [7:0] b;integer j;reg [15:0] c;
  begin c=value^{b,8'h00};for(j=0;j<8;j=j+1)c=c[15]?(c<<1)^16'h1021:c<<1;crc_byte=c;end
 endfunction
 assign rx_ready=!rst && !waiting_reply && !request_valid && tx_state==0;
 // Existing controllers use response_ready both for request admission and for
 // their eventual reply. The transmit slot is already empty on the request
 // cycle; gating this with waiting_reply would lose that request permanently.
 assign response_ready=!rst && tx_state==0;
 assign tx_valid=!rst && tx_state==2;
 assign tx_data=tx_frame[7:0];
 always @(posedge clk)begin
  if(rst)begin
   request<=0;request_valid<=0;waiting_reply<=0;rx_frame<=0;tx_frame<=0;
   rx_index<=0;tx_index<=0;crc_index<=0;rx_crc<=16'hffff;tx_crc<=16'hffff;
   timeout_count<=0;tx_state<=0;
  end else begin
   if(request_valid && request_ready)begin request_valid<=0;waiting_reply<=1;end
   if(rx_ready && rx_index!=0)begin
    if(timeout_count==TIMEOUT_CYCLES-1)begin rx_index<=0;timeout_count<=0;end
    else timeout_count<=timeout_count+1'b1;
   end
   if(rx_valid && rx_ready)begin
    timeout_count<=0;
    if(rx_index==0)begin if(rx_data==8'ha5)begin rx_frame[7:0]<=rx_data;rx_index<=1;end end
    else if(rx_index==1)begin
     if(rx_data==8'h5a)begin rx_frame[15:8]<=rx_data;rx_index<=2;rx_crc<=16'hffff;end
     else if(rx_data!=8'ha5)rx_index<=0;
    end else begin
     rx_frame[rx_index*8+:8]<=rx_data;
     if(rx_index<30)rx_crc<=crc_byte(rx_crc,rx_data);
     if(rx_index==31)begin
      rx_index<=0;
      if({rx_data,rx_frame[247:240]}==rx_crc)begin request<={rx_data,rx_frame[247:0]};request_valid<=1;end
     end else rx_index<=rx_index+1'b1;
    end
   end
   case(tx_state)
    0:if(response_valid && response_ready)begin tx_frame<={16'h0,response};tx_crc<=16'hffff;crc_index<=2;tx_state<=1;end
    1:begin
     tx_crc<=crc_byte(tx_crc,tx_frame[crc_index*8+:8]);
     if(crc_index==29)begin tx_frame[255:240]<=crc_byte(tx_crc,tx_frame[crc_index*8+:8]);tx_index<=0;tx_state<=2;end
     else crc_index<=crc_index+1'b1;
    end
    2:if(tx_ready)begin
     tx_frame<={8'hff,tx_frame[255:8]};
     if(tx_index==31)begin tx_state<=0;waiting_reply<=0;end
     else tx_index<=tx_index+1'b1;
    end
   endcase
  end
 end
endmodule
