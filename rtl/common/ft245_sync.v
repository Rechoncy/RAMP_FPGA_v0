`timescale 1ns/1ps
// FT232H synchronous 245 FIFO, clocked by CLKOUT (60 MHz).
// Datasheet DS_FT232H section 4.4: OE# precedes RD# by a full cycle.
// Registered output controls/data give a full cycle of setup. Two RX slots
// reserve space for the next registered RD# cycle, even if downstream ready
// disappears. One turnaround cycle releases the shared data bus. RXF#/TXE#
// are synchronous flags, never passed through a two-flop data synchronizer.
module ft245_sync(
 input clk,rst,inout [7:0] usb_data,input usb_rxf_n,usb_txe_n,
 output usb_oe_n,usb_rd_n,usb_wr_n,usb_siwu_n,
 output [7:0] rx_data,output rx_valid,input rx_ready,
 input [7:0] tx_data,input tx_valid,output tx_ready);
 localparam IDLE=0,READ_SETUP=1,READ_ACTIVE=2,TURN=3,WRITE_SETUP=4,WRITE_ACTIVE=5;
 reg [2:0] state,next_state;
 (* IOB="TRUE" *) reg [7:0] write_data;
 (* IOB="TRUE" *) reg read_n,write_n,output_n;
 (* IOB="TRUE" *) reg drive_data;
 reg [7:0] receive_first,receive_second;
 reg [1:0] receive_count;
 wire read_accept=!read_n && !usb_rxf_n;
 wire receive_pop=rx_valid && rx_ready;
 wire [2:0] receive_after={1'b0,receive_count}+{2'b0,read_accept}-{2'b0,receive_pop};
 assign usb_siwu_n=1'b1;
 assign usb_oe_n=output_n;
 assign usb_rd_n=read_n;
 assign usb_wr_n=write_n;
 assign usb_data=drive_data ? write_data:8'bz;
 assign rx_data=receive_first;
 assign rx_valid=!rst && receive_count!=0;
 assign tx_ready=!rst && !write_n && !usb_txe_n;
 always @*begin
  next_state=state;
  case(state)
   IDLE:if(tx_valid && !usb_txe_n)next_state=WRITE_SETUP;
        else if(!usb_rxf_n && receive_after<2)next_state=READ_SETUP;
   READ_SETUP:next_state=READ_ACTIVE;
   READ_ACTIVE:if(usb_rxf_n || tx_valid)next_state=TURN;
   TURN:next_state=IDLE;
   WRITE_SETUP:next_state=WRITE_ACTIVE;
   WRITE_ACTIVE:if(!usb_txe_n)next_state=TURN;
   default:next_state=IDLE;
  endcase
 end
 always @(posedge clk or posedge rst)begin
  if(rst)begin
   state<=IDLE;write_data<=0;read_n<=1;write_n<=1;output_n<=1;drive_data<=0;
   receive_first<=0;receive_second<=0;receive_count<=0;
  end else begin
   state<=next_state;
   output_n<=!(next_state==READ_SETUP || next_state==READ_ACTIVE);
   write_n<=next_state!=WRITE_ACTIVE;
   drive_data<=next_state==WRITE_SETUP || next_state==WRITE_ACTIVE;
   // Account for THIS edge's accepted byte and FIFO write before reserving a
   // slot for NEXT edge. A full downstream FIFO can never cause a third byte.
   read_n<=!(next_state==READ_ACTIVE && receive_after<2);
   if(state==IDLE && next_state==WRITE_SETUP)write_data<=tx_data;
   receive_count<=receive_after[1:0];
   if(receive_pop && receive_count==2)receive_first<=receive_second;
   if(read_accept)begin
    if(receive_count==0 || (receive_pop && receive_count==1))receive_first<=usb_data;
    else receive_second<=usb_data;
   end
  end
 end
endmodule
