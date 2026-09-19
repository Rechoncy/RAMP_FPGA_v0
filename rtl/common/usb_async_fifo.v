`timescale 1ns/1ps
// Small byte CDC buffer. Gray pointers only cross clock domains. Each reset
// must assert together and deassert synchronously to its respective clock.
// Source Gray-to-first-synchronizer paths require max_delay/bus_skew constraints.
(* KEEP_HIERARCHY="yes" *) module usb_async_fifo #(parameter WIDTH=8, ADDR_BITS=9)(
 input wr_clk,wr_rst,input [WIDTH-1:0] wr_data,input wr_valid,output wr_ready,
 input rd_clk,rd_rst,output [WIDTH-1:0] rd_data,output rd_valid,input rd_ready);
 localparam PTR_BITS=ADDR_BITS+1;
 (* ram_style="distributed" *) reg [WIDTH-1:0] memory[0:(1<<ADDR_BITS)-1];
 reg [PTR_BITS-1:0] wr_binary,wr_gray,rd_binary,rd_gray;
 (* ASYNC_REG="TRUE" *) reg [PTR_BITS-1:0] rd_gray_sync1,rd_gray_sync2;
 (* ASYNC_REG="TRUE" *) reg [PTR_BITS-1:0] wr_gray_sync1,wr_gray_sync2;
 reg full;
 wire push=wr_valid && wr_ready, pop=rd_valid && rd_ready;
 wire [PTR_BITS-1:0] wr_next=wr_binary+push;
 wire [PTR_BITS-1:0] wr_gray_next=(wr_next>>1)^wr_next;
 wire [PTR_BITS-1:0] rd_next=rd_binary+pop;
 assign wr_ready=!full && !wr_rst;
 assign rd_valid=rd_gray!=wr_gray_sync2 && !rd_rst;
 assign rd_data=memory[rd_binary[ADDR_BITS-1:0]];
 // Storage has no reset: resetting its pointers makes stale bytes unreachable.
 // Keep the write process separate from asynchronous pointer reset so Vivado
 // can infer distributed dual-clock RAM instead of thousands of flip-flops.
 always @(posedge wr_clk)if(push)memory[wr_binary[ADDR_BITS-1:0]]<=wr_data;
 always @(posedge wr_clk or posedge wr_rst) begin
  if(wr_rst)begin wr_binary<=0;wr_gray<=0;full<=0;rd_gray_sync1<=0;rd_gray_sync2<=0;end
  else begin
   rd_gray_sync1<=rd_gray;rd_gray_sync2<=rd_gray_sync1;
   wr_binary<=wr_next;wr_gray<=wr_gray_next;
   full<=wr_gray_next=={~rd_gray_sync2[PTR_BITS-1:PTR_BITS-2],rd_gray_sync2[PTR_BITS-3:0]};
  end
 end
 always @(posedge rd_clk or posedge rd_rst) begin
  if(rd_rst)begin rd_binary<=0;rd_gray<=0;wr_gray_sync1<=0;wr_gray_sync2<=0;end
  else begin
   wr_gray_sync1<=wr_gray;wr_gray_sync2<=wr_gray_sync1;
   rd_binary<=rd_next;rd_gray<=(rd_next>>1)^rd_next;
  end
 end
endmodule
