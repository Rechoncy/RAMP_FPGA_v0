`timescale 1ns/1ps
// Two physical transports, one command engine. UART retains its stop-and-wait
// contract (one unacknowledged request); USB requests use ready backpressure.
// Exactly one controller transaction is in flight and its reply goes only to
// its originating interface. Configure the board from one host at a time:
// channel selection and pending waveform state are shared, not per-interface.
module dual_packet_router(
 input clk,rst,
 input [255:0] uart_request,input uart_request_valid,
 output [239:0] uart_response,output uart_response_valid,input uart_response_ready,
 input [255:0] usb_request,input usb_request_valid,output usb_request_ready,
 output [239:0] usb_response,output usb_response_valid,input usb_response_ready,
 output [255:0] request,output request_valid,
 input [239:0] response,input response_valid,output response_ready);
 localparam IDLE=0,ISSUE=1,WAIT_REPLY=2,SEND_REPLY=3;
 reg [1:0] state;
 reg uart_pending,source,last_source;
 reg [255:0] uart_buffer,current_request;
 reg [239:0] current_response;
 wire grant_uart=state==IDLE && uart_pending && (!usb_request_valid || last_source);
 wire grant_usb=state==IDLE && usb_request_valid && (!uart_pending || !last_source);
 assign usb_request_ready=!rst && state==IDLE && (!uart_pending || !last_source);
 assign request=current_request;
 assign request_valid=!rst && state==ISSUE;
 // The existing engine also requires an available response slot on admission.
 assign response_ready=!rst && (state==ISSUE || state==WAIT_REPLY);
 assign uart_response=current_response;
 assign usb_response=current_response;
 assign uart_response_valid=!rst && state==SEND_REPLY && !source;
 assign usb_response_valid=!rst && state==SEND_REPLY && source;
 always @(posedge clk)begin
  if(rst)begin
   state<=IDLE;uart_pending<=0;source<=0;last_source<=1;
   uart_buffer<=0;current_request<=0;current_response<=0;
  end else begin
   if(uart_request_valid && (!uart_pending || grant_uart))begin
    uart_buffer<=uart_request;uart_pending<=1;
   end
   case(state)
    IDLE:if(grant_uart)begin
     // Bit 240 was part of the already-validated wire CRC. It is now an
     // internal source tag; no external packet format or CRC is changed.
     current_request<={uart_buffer[255:241],1'b0,uart_buffer[239:0]};
     uart_pending<=uart_request_valid;source<=0;last_source<=0;state<=ISSUE;
    end else if(grant_usb)begin
     current_request<={usb_request[255:241],1'b1,usb_request[239:0]};
     source<=1;last_source<=1;state<=ISSUE;
    end
    ISSUE:state<=WAIT_REPLY;
    WAIT_REPLY:if(response_valid)begin current_response<=response;state<=SEND_REPLY;end
    SEND_REPLY:if(source ? usb_response_ready:uart_response_ready)state<=IDLE;
   endcase
  end
 end
endmodule
