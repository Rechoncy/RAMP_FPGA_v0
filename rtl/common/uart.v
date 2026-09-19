`timescale 1ns/1ps
module uart_rx #(parameter CLK_HZ=200000000, BAUD=115200)(
 input clk, rst, rx, output reg [7:0] data, output reg valid);
 localparam DIV=(CLK_HZ+BAUD/2)/BAUD;
 (* ASYNC_REG="TRUE" *) reg [1:0] sync_ff;
 reg [15:0] count; reg [3:0] bitno; reg [7:0] shift; reg [1:0] state;
 always @(posedge clk) begin
  if(rst) begin sync_ff<=3; count<=0; bitno<=0; shift<=0; data<=0; valid<=0; state<=0; end
  else begin
   sync_ff<={sync_ff[0],rx}; valid<=0;
   case(state)
    0: if(!sync_ff[1]) begin count<=DIV/2-1; state<=1; end
    1: if(count!=0) count<=count-1'b1;
       else if(!sync_ff[1]) begin count<=DIV-1; bitno<=0; state<=2; end else state<=0;
    2: if(count!=0) count<=count-1'b1;
       else begin shift[bitno]<=sync_ff[1]; count<=DIV-1;
        if(bitno==7) state<=3; else bitno<=bitno+1'b1; end
    3: if(count!=0) count<=count-1'b1;
       else begin if(sync_ff[1]) begin data<=shift; valid<=1; end state<=0; end
   endcase
  end
 end
endmodule

module uart_tx #(parameter CLK_HZ=200000000, BAUD=115200)(
 input clk,rst,input [7:0] data,input valid,output ready,output tx);
 localparam DIV=(CLK_HZ+BAUD/2)/BAUD;
 reg [9:0] shift; reg [15:0] count; reg [3:0] remaining;
 assign tx=remaining==0 ? 1'b1:shift[0];
 assign ready=remaining==0;
 always @(posedge clk) begin
  if(rst) begin remaining<=0; shift<=10'h3ff; count<=0; end
  else if(remaining==0) begin
   if(valid) begin shift<={1'b1,data,1'b0}; remaining<=10; count<=DIV-1; end
  end else if(count!=0) count<=count-1'b1;
  else begin count<=DIV-1; shift<={1'b1,shift[9:1]}; remaining<=remaining-1'b1; end
 end
endmodule
