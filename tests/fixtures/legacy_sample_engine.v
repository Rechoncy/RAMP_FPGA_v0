`timescale 1ns/1ps
// Lossless run-length playback: each descriptor holds one DAC code for N clocks.
// A four-entry prefetch FIFO sustains a new descriptor on EVERY 200 MHz edge.
module sample_engine #(parameter DAC_BITS=10)(
 input clk,rst,arm,disarm,abort_run,soft_trigger,external_trigger,
 input active_valid,external_mode,input [DAC_BITS-1:0] initial_code,safe_code,
 input [15:0] repeats,input [13:0] run_count,
 output wire read_en,output reg [12:0] read_index,input [DAC_BITS+31:0] descriptor,
 output reg [1:0] state,output wire prepared,output reg [DAC_BITS-1:0] code,
 output reg [12:0] run_index,output reg [15:0] completed_loops,
 output reg [31:0] ignored);
 reg [DAC_BITS+31:0] fifo [0:3];
 reg [1:0] rd_ptr,wr_ptr;
 reg [2:0] occupancy;
 reg inflight;
 reg [31:0] remaining;
 reg [12:0] last_index;
 reg [15:0] repeat_limit;
 reg repeat_forever;
 reg [15:0] round_loops;
 reg final_loop;
 // Configuration is stable throughout ARM/prefetch and playback. Compute
 // count-1 before triggering so one-clock runs never need add-then-compare.
 always @(posedge clk) begin
  last_index<=run_count-1'b1;
  repeat_limit<=repeats-16'd2;
  repeat_forever<=(repeats==0);
 end
 wire [DAC_BITS+31:0] head=fifo[rd_ptr];
 wire trigger_event=external_mode ? external_trigger:soft_trigger;
 wire final_run=(run_index==last_index);
 // Decide whether the NEXT round is final at the previous round's boundary.
 // The flag is ready even when a whole table consists of one one-clock run,
 // keeping the 16-bit comparator out of the DAC/FIFO advance path at 200 MHz.
 wire finish=(state==2 && remaining==1 && final_run && final_loop);
 assign prepared=(state==1 && occupancy>=3);
 wire start_event=prepared && trigger_event;
 wire advance=(state==2 && remaining==1 && !(final_run && final_loop));
 wire pop=start_event || advance;
 assign read_en=(state==1 || state==2) && !arm && !abort_run && !disarm &&
                ({1'b0,occupancy}+inflight<4);
 always @(posedge clk) begin
  if(rst) begin
   state<=0; code<=(1 << (DAC_BITS-1)); read_index<=0; run_index<=0; completed_loops<=0; ignored<=0;
   rd_ptr<=0; wr_ptr<=0; occupancy<=0; inflight<=0; remaining<=0; round_loops<=0; final_loop<=0;
  end else if(abort_run || disarm || arm) begin
   state<=(arm && active_valid) ? 1:0;
   code<=abort_run ? safe_code:initial_code;
   read_index<=0; run_index<=0; completed_loops<=0; ignored<=0;
   rd_ptr<=0; wr_ptr<=0; occupancy<=0; inflight<=0; remaining<=0; round_loops<=0; final_loop<=0;
  end else if(finish && external_mode) begin
   // Flush speculative reads: the next external edge starts descriptor zero.
   // Refill takes several clocks; edges before prepared are not queued.
   state<=1; code<=initial_code; read_index<=0; run_index<=0; round_loops<=0; final_loop<=0;
   rd_ptr<=0; wr_ptr<=0; occupancy<=0; inflight<=0; remaining<=0;
   if(completed_loops!=16'hffff) completed_loops<=completed_loops+1'b1;
   if(trigger_event) ignored<=ignored+1'b1;
  end else begin
   if(state!=2) final_loop<=(repeats==1);
   inflight<=read_en;
   if(read_en) read_index<=(read_index==last_index) ? 13'd0:read_index+1'b1;
   if(inflight) begin fifo[wr_ptr]<=descriptor; wr_ptr<=wr_ptr+1'b1; end
   if(pop) rd_ptr<=rd_ptr+1'b1;
   case({inflight,pop})
    2'b10: occupancy<=occupancy+1'b1;
    2'b01: occupancy<=occupancy-1'b1;
   endcase
   if(start_event) begin
    state<=2; code<=head[DAC_BITS+31:32]; remaining<=head[31:0]; run_index<=0;
   end else if(state==2) begin
    if(trigger_event) ignored<=ignored+1'b1;
    if(remaining==1) begin
     if(final_run) begin
      if(completed_loops!=16'hffff) completed_loops<=completed_loops+1'b1;
      round_loops<=round_loops+1'b1; run_index<=0;
      // Zero stays infinite across every counter wrap and display saturation.
      final_loop<=!repeat_forever && (round_loops==repeat_limit);
      if(final_loop) begin state<=3; code<=initial_code; end
     end else run_index<=run_index+1'b1;
     if(advance) begin code<=head[DAC_BITS+31:32]; remaining<=head[31:0]; end
    end else remaining<=remaining-1'b1;
   end
  end
 end
endmodule
