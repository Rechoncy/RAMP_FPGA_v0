`timescale 1ns/1ps
// Link-only training: >=10 ms BREAK, repeated 0x55, then >=12 idle bit cells.
// No clocks are switched and no playback/controller reset is asserted.
module uart_autobaud #(parameter CLK_HZ=200000000, BAUD=115200)(
 input clk,rst,rx,link_idle,
 output reg [15:0] divider,
 output pause_rx);
 localparam DEFAULT_DIV=(CLK_HZ+BAUD/2)/BAUD;
 localparam BREAK_TICKS=CLK_HZ/100, TRAIN_TIMEOUT=CLK_HZ/4;
 localparam NORMAL=0, BREAK=1, EDGES=2, DRAIN=3;
 (* ASYNC_REG="TRUE" *) reg [1:0] sync_ff;
 reg previous;
 reg [1:0] state;
 reg [25:0] low_ticks,age;
 reg [23:0] quiet_ticks;
 reg [15:0] edge_ticks,candidate,pending_div;
 reg [4:0] qualified_count;
 reg [3:0] stable_intervals;
 reg have_edge,restart;
 assign pause_rx=state!=NORMAL || restart;

 // Quantize to the explicit supported list; never accept an arbitrary glitch
 // width as a divider. 2% plus one core tick accommodates synchronization.
 function [15:0] classify;
  input [16:0] width;
  integer i,baud,nominal,tolerance;
  begin
   classify=0;
   for(i=0;i<9;i=i+1) begin
    case(i)
     0:baud=9600;1:baud=19200;2:baud=38400;3:baud=57600;4:baud=115200;
     5:baud=230400;6:baud=460800;7:baud=921600;default:baud=1000000;
    endcase
    nominal=(CLK_HZ+baud/2)/baud;tolerance=(nominal*2+99)/100+1;
    if(width>=nominal-tolerance && width<=nominal+tolerance) classify=nominal;
   end
  end
 endfunction
 reg interval_valid;
 reg [15:0] interval_div;
 reg interval_high,interval_long;
 always @(posedge clk) begin
  if(rst) begin
   sync_ff<=3;previous<=1;state<=NORMAL;divider<=DEFAULT_DIV;
   low_ticks<=0;age<=0;quiet_ticks<=0;edge_ticks<=0;candidate<=0;pending_div<=DEFAULT_DIV;
   qualified_count<=0;stable_intervals<=0;have_edge<=0;restart<=0;interval_valid<=0;interval_div<=0;
   interval_high<=0;interval_long<=0;
  end else begin
   sync_ff<={sync_ff[0],rx};previous<=sync_ff[1];restart<=0;interval_valid<=0;
   if(sync_ff[1]) low_ticks<=0;
   else if(low_ticks<BREAK_TICKS)low_ticks<=low_ticks+1'b1;
   if(state!=NORMAL && state!=BREAK)age<=age+1'b1;else age<=0;
   if(!sync_ff[1] && low_ticks==BREAK_TICKS-1) begin
    state<=BREAK;qualified_count<=0;stable_intervals<=0;have_edge<=0;quiet_ticks<=0;candidate<=0;
   end else if(state!=NORMAL && state!=BREAK && age==TRAIN_TIMEOUT-1) begin
    // Malformed/incomplete training recovers the previous baud, never changes
    // the divider, and clears a partial receiver frame before accepting data.
    state<=NORMAL;restart<=1;qualified_count<=0;stable_intervals<=0;
   end else case(state)
    NORMAL: begin qualified_count<=0;stable_intervals<=0;have_edge<=0;end
    BREAK: if(sync_ff[1])begin state<=EDGES;have_edge<=0;edge_ticks<=0;end
    EDGES: begin
     if(have_edge && edge_ticks!=16'hffff)edge_ticks<=edge_ticks+1'b1;
     if(sync_ff[1]!=previous) begin
      if(have_edge)begin
       interval_div<=classify({1'b0,edge_ticks}+17'd1);interval_valid<=1;
       interval_high<=previous;
       interval_long<=({1'b0,edge_ticks}+17'd1)>=({1'b0,candidate}+({1'b0,candidate}>>1));
      end
      have_edge<=1;edge_ticks<=0;
     end
     // One stage separates the constant comparator network from qualification.
     if(interval_valid)begin
      // CH340 can add one or more idle high bit cells between 0x55 bytes.
      // Accept that gap only after nine stable intervals (a full start/data
      // byte); it contributes no evidence. A short burst, low gap or changed
      // data-bit period still clears qualification. Never classify a 2-bit
      // idle as a request to switch to half the baud rate.
      if(candidate!=0 && interval_high && interval_long && stable_intervals==9)
       stable_intervals<=0;
      else if(interval_div==0)begin candidate<=0;qualified_count<=0;stable_intervals<=0;end
      else if(interval_div!=candidate)begin candidate<=interval_div;qualified_count<=1;stable_intervals<=1;end
      else if(qualified_count==23)begin pending_div<=candidate;quiet_ticks<=0;state<=DRAIN;end
      else begin
       qualified_count<=qualified_count+1'b1;
       if(stable_intervals<9)stable_intervals<=stable_intervals+1'b1;
      end
     end
    end
    DRAIN: begin
     if(!sync_ff[1])quiet_ticks<=0;
     else if(quiet_ticks<({8'b0,pending_div}*24'd12))quiet_ticks<=quiet_ticks+1'b1;
     else if(link_idle)begin divider<=pending_div;state<=NORMAL;restart<=1;end
    end
   endcase
  end
 end
endmodule

// The divider is changed only with an idle transmitter and reset receiver.
// Keep the original fixed-divider UART modules unchanged for old integrations.
module uart_rx_dynamic(input clk,rst,rx,input [15:0] divider,
 output reg [7:0] data,output reg valid);
 (* ASYNC_REG="TRUE" *) reg [1:0] sync_ff;
 reg [15:0] count;reg [3:0] bitno;reg [7:0] shift;reg [1:0] state;
 always @(posedge clk) begin
  if(rst)begin sync_ff<=3;count<=0;bitno<=0;shift<=0;data<=0;valid<=0;state<=0;end
  else begin
   sync_ff<={sync_ff[0],rx};valid<=0;
   case(state)
    0:if(!sync_ff[1])begin count<=(divider>>1)-1'b1;state<=1;end
    1:if(count!=0)count<=count-1'b1;
      else if(!sync_ff[1])begin count<=divider-1'b1;bitno<=0;state<=2;end else state<=0;
    2:if(count!=0)count<=count-1'b1;
      else begin shift[bitno]<=sync_ff[1];count<=divider-1'b1;
       if(bitno==7)state<=3;else bitno<=bitno+1'b1;end
    3:if(count!=0)count<=count-1'b1;
      else begin if(sync_ff[1])begin data<=shift;valid<=1;end state<=0;end
   endcase
  end
 end
endmodule

module uart_tx_dynamic(input clk,rst,input [15:0] divider,input [7:0] data,
 input valid,output ready,output tx);
 reg [9:0] shift;reg [15:0] count;reg [3:0] remaining;
 assign tx=remaining==0 ? 1'b1:shift[0];assign ready=remaining==0;
 always @(posedge clk) begin
  if(rst)begin remaining<=0;shift<=10'h3ff;count<=0;end
  else if(remaining==0)begin
   if(valid)begin shift<={1'b1,data,1'b0};remaining<=10;count<=divider-1'b1;end
  end else if(count!=0)count<=count-1'b1;
  else begin count<=divider-1'b1;shift<={1'b1,shift[9:1]};remaining<=remaining-1'b1;end
 end
endmodule
