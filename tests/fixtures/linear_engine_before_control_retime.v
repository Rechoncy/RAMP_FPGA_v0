`timescale 1ns/1ps
// Target-and-duration playback. The external table stays DAC_BITS+32 bits.
// For S -> E in N clocks, emit round(S + (E-S)*k/N), k=0..N-1.
// The following segment begins with E on the very next edge. Ties round
// toward the greater (nonnegative) DAC code, for both slope directions.
//
// COMMIT must reject N < 32. A 2-entry register FIFO contains precomputed
// integer quotient/remainder parameters, not additional waveform slots.
// Preparing a descriptor takes DAC_BITS+5 clocks (<=19 for supported DACs),
// so even adjacent 32-clock segments never stall. BRAM reads are synchronous
// with one clock latency. ARM stays unprepared until both entries are ready.
module linear_engine #(parameter DAC_BITS=10)(
 input clk,rst,arm,disarm,abort_run,soft_trigger,external_trigger,
 input rising,falling,active_valid,external_mode,gated,
 input [DAC_BITS-1:0] initial_code,safe_code,target_code,
 input [15:0] repeats,input [13:0] run_count,rise_count,
 input [31:0] fall_ticks,
 output wire read_en,output reg [12:0] read_index,
 input [DAC_BITS+31:0] descriptor,
 output reg [1:0] state,output wire prepared,
 output reg [DAC_BITS-1:0] code,output reg [12:0] run_index,
 output reg [15:0] completed_loops,output reg [31:0] ignored,
 output reg [2:0] phase);
 localparam UP=1,HOLD=2,DOWN=3,DIVIDE=4,LINEAR=5,SETUP=6;
 localparam FETCH=0,MEMORY=1,CAPTURE=2,QUOTIENT=3,PREPARE=4,PUSH=5;

 reg [DAC_BITS-1:0] fifo_start[0:1],fifo_inc[0:1],fifo_inc_carry[0:1];
 reg [31:0] fifo_ticks[0:1],fifo_rem[0:1];
 reg [32:0] fifo_negative[0:1],fifo_error[0:1];
 reg [12:0] fifo_index[0:1];
 reg fifo_last[0:1],fifo_rise_last[0:1];
 reg rd_ptr,wr_ptr;
 reg [1:0] occupancy;
 reg [2:0] producer;
 reg [DAC_BITS+31:0] p_descriptor;
 reg [12:0] p_index;
 reg [DAC_BITS-1:0] p_start,next_start,p_dividend,p_quotient,p_q;
 reg [DAC_BITS:0] p_div_rem;
 reg [31:0] p_ticks,p_r,p_half;
 reg [32:0] p_negative;
 reg [4:0] p_left;
 reg p_negative_slope;
 reg [DAC_BITS-1:0] p_inc,p_inc_carry;
 wire [DAC_BITS:0] p_trial={p_div_rem[DAC_BITS-1:0],p_dividend[DAC_BITS-1]};
 wire [DAC_BITS+1:0] p_difference={1'b0,p_trial}-{1'b0,p_ticks[DAC_BITS:0]};
 wire p_subtract=!(|p_ticks[31:DAC_BITS+1]) && !p_difference[DAC_BITS+1];
 wire [DAC_BITS:0] p_remainder_next=p_subtract ? p_difference[DAC_BITS:0]:p_trial;
 wire [DAC_BITS-1:0] p_quotient_next={p_quotient[DAC_BITS-2:0],p_subtract};

 reg [31:0] remaining,step_r;
 reg remaining_last,at_last,at_rise_last;
 reg [DAC_BITS-1:0] increment,increment_carry;
 reg [32:0] error_acc,negative_step;
 // Carry is the registered sign of a one-sample lookahead remainder. There
 // is no remainder add -> compare -> DAC add chain in the 200 MHz path.
 wire carry=!error_acc[32];
 reg [12:0] last_index;
 reg [15:0] repeat_limit,round_loops;
 reg repeat_forever,final_loop;
 always @(posedge clk) begin
  last_index<=run_count-1'b1;
  repeat_limit<=repeats-16'd2;
  repeat_forever<=repeats==0;
 end
 wire trigger_event=gated ? rising:(external_mode ? external_trigger:soft_trigger);
 assign prepared=state==1 && occupancy==2;
 wire start=prepared && trigger_event && !(gated && falling);
 wire up_end=gated && phase==UP && remaining_last && at_rise_last;
 wire start_down=gated && state==2 && falling && (phase==HOLD || up_end);
 wire early=gated && state==2 && falling && phase==UP && !up_end;
 wire fixed_finish=!gated && state==2 && remaining_last && at_last && final_loop;
 wire gate_finish=gated && state==2 &&
                  ((phase==DOWN && remaining_last && at_last) ||
                   (phase==LINEAR && remaining_last));
 wire finish=fixed_finish || gate_finish;
 wire advance=state==2 && remaining_last && !early && !finish &&
              (!gated || (phase==UP && !up_end) || (phase==DOWN && !at_last));
 wire pop=start || start_down || advance;
 wire push=producer==PUSH;
 assign read_en=(state==1 || (state==2 && (!gated || phase<=DOWN))) &&
                producer==FETCH && occupancy<2 && !arm && !disarm && !abort_run && !finish;

 // Early falling uses the legacy gate_engine algorithm, deliberately floor
 // rounding the magnitude and preserving its DAC_BITS division + one setup
 // clock latency. Only normal table segments use the new rounded semantics.
 reg [DAC_BITS-1:0] dividend,quotient;
 reg [DAC_BITS:0] div_rem;
 reg [4:0] div_left;
 reg direction;
 wire [DAC_BITS:0] trial={div_rem[DAC_BITS-1:0],dividend[DAC_BITS-1]};
 wire [DAC_BITS+1:0] difference={1'b0,trial}-{1'b0,fall_ticks[DAC_BITS:0]};
 wire subtract=!(|fall_ticks[31:DAC_BITS+1]) && !difference[DAC_BITS+1];
 wire [DAC_BITS:0] remainder_next=subtract ? difference[DAC_BITS:0]:trial;
 wire [DAC_BITS-1:0] quotient_next={quotient[DAC_BITS-2:0],subtract};
 reg [DAC_BITS-1:0] early_q;

 // Keep the early-return arithmetic primed while outside DIVIDE. On the
 // falling edge this captures the SAME pre-edge code as the legacy player;
 // the following DAC_BITS clocks perform the same division. Its datapath
 // reset/enable depends only on the registered local phase, not the external
 // trigger mux, pop/advance priority tree, or another event-step engine.
 // Work on a coincident ARM/abort is harmless: phase exits DIVIDE and these
 // private registers are primed again before any future early return.
 always @(posedge clk) begin
  if(rst || phase!=DIVIDE) begin
   dividend<=code>=initial_code ? code-initial_code:initial_code-code;
   direction<=code>=initial_code;
   quotient<=0; div_rem<=0; div_left<=DAC_BITS;
  end else begin
   dividend<=dividend<<1; quotient<=quotient_next;
   div_rem<=remainder_next; div_left<=div_left-1'b1;
  end
  if(rst) early_q<=0;
  else if(phase==DIVIDE && div_left==1) early_q<=quotient_next;
 end

 // Preload the current FIFO head while waiting for a trigger or gate fall.
 // In a timed phase, decrement on every nonfinal clock and naturally load
 // the next head at a segment boundary. No external trigger/pop/ARM priority
 // tree drives the 32-bit counter's CE. Unused counts during abort, prefetch
 // or early-return setup are private; prepared gates all first consumption.
 always @(posedge clk) begin
  if(phase==DIVIDE || phase==SETUP) begin
   remaining<=fall_ticks; remaining_last<=fall_ticks==1;
  end else if((phase==UP || phase==DOWN || phase==LINEAR) && !remaining_last) begin
   remaining<=remaining-1'b1; remaining_last<=remaining==2;
  end else begin
   remaining<=fifo_ticks[rd_ptr]; remaining_last<=fifo_ticks[rd_ptr]==1;
  end
 end

 // Prime interpolation parameters during idle/HOLD and at each segment
 // boundary. Only local phase/last-clock decoding controls these registers.
 // During an early falling edge, an extra private remainder update is safe:
 // DIVIDE/SETUP replace it completely before the first return increment.
 always @(posedge clk) begin
  case(phase)
   DIVIDE: step_r<=remainder_next;
   SETUP: begin
    negative_step<={1'b0,step_r}-{1'b0,fall_ticks};
    increment<=direction ? -early_q:early_q;
    increment_carry<=direction ? -early_q-1'b1:early_q+1'b1;
   end
   LINEAR: begin end
   default: if((phase!=UP && phase!=DOWN) || remaining_last) begin
    step_r<=fifo_rem[rd_ptr]; negative_step<=fifo_negative[rd_ptr];
    increment<=fifo_inc[rd_ptr]; increment_carry<=fifo_inc_carry[rd_ptr];
   end
  endcase
  if(phase==SETUP) error_acc<={1'b0,step_r}-{1'b0,fall_ticks};
  else if(phase==LINEAR || ((phase==UP || phase==DOWN) && !remaining_last))
   error_acc<=error_acc+(carry ? negative_step:{1'b0,step_r});
  else error_acc<=fifo_error[rd_ptr];
 end

 // ARM already presents I and HOLD already presents A. Therefore a trigger
 // need not mux a new code into this datapath: it only changes phase. Normal
 // segment boundaries load the precomputed next start; early falling alone
 // freezes the current UP sample. Removing pop from this update cuts the
 // event sequencer's readiness feedback out of the DAC register enable.
 always @(posedge clk) begin
  if(rst) code<=(1 << (DAC_BITS-1));
  else if(arm || disarm || abort_run || finish) code<=abort_run ? safe_code:initial_code;
  else case(phase)
   UP,DOWN: if(!(phase==UP && gated && falling && !(remaining_last && at_rise_last))) begin
    if(remaining_last) code<=fifo_start[rd_ptr];
    else code<=code+(carry ? increment_carry:increment);
   end
   HOLD: code<=target_code;
   LINEAR: code<=code+(carry ? increment_carry:increment);
  endcase
 end

 always @(posedge clk) begin
  if(rst) begin
   state<=0; phase<=0; read_index<=0; run_index<=0;
   completed_loops<=0; ignored<=0; rd_ptr<=0; wr_ptr<=0; occupancy<=0; producer<=FETCH;
   at_last<=0; at_rise_last<=0; round_loops<=0; final_loop<=0;
   next_start<=0;
   p_index<=0; p_start<=0; p_dividend<=0; p_quotient<=0; p_q<=0; p_div_rem<=0;
   p_ticks<=0; p_r<=0; p_half<=0; p_negative<=0; p_left<=0; p_negative_slope<=0;
   p_inc<=0; p_inc_carry<=0; p_descriptor<=0;
  end else if(arm || disarm || abort_run || finish) begin
   state<=abort_run || disarm ? 0:
          arm ? (active_valid ? 1:0):
          (gated || external_mode) ? 1:3;
   phase<=0;
   read_index<=0; run_index<=0; rd_ptr<=0; wr_ptr<=0; occupancy<=0; producer<=FETCH;
   next_start<=initial_code; at_last<=0; at_rise_last<=0;
   round_loops<=0; final_loop<=0;
   if(arm || disarm || abort_run) begin completed_loops<=0; ignored<=0; end
   else begin
    if(completed_loops!=16'hffff) completed_loops<=completed_loops+1'b1;
    if(trigger_event && !gated) ignored<=ignored+1'b1;
   end
  end else begin
   if(state!=2) final_loop<=repeats==1;
   if(pop) rd_ptr<=!rd_ptr;
   case({push,pop})
    2'b10: occupancy<=occupancy+1'b1;
    2'b01: occupancy<=occupancy-1'b1;
   endcase
   case(producer)
    FETCH: if(read_en) begin
     p_start<=gated && read_index==rise_count ? target_code:next_start;
     p_index<=read_index;
     read_index<=read_index==last_index ? 13'd0:read_index+1'b1;
     producer<=MEMORY;
    end
    MEMORY: begin
     // Isolate the BRAM clock-to-out and routing from the 14-bit compare /
     // absolute-difference logic. This adds one preparation clock only;
     // descriptor playback still advances every N clocks without a gap.
     p_descriptor<=descriptor; producer<=CAPTURE;
    end
    CAPTURE: begin
     p_ticks<=p_descriptor[31:0];
     p_negative_slope<=p_descriptor[DAC_BITS+31:32]<p_start;
     p_dividend<=p_descriptor[DAC_BITS+31:32]>=p_start ? p_descriptor[DAC_BITS+31:32]-p_start:
                                                                    p_start-p_descriptor[DAC_BITS+31:32];
     // N-bias: ceil(N/2) for positive, floor(N/2)+1 for negative.
     p_half<={1'b0,p_descriptor[31:1]}+
             ((p_descriptor[DAC_BITS+31:32]<p_start) || p_descriptor[0]);
     next_start<=p_index==last_index ? initial_code:p_descriptor[DAC_BITS+31:32];
     p_quotient<=0; p_div_rem<=0; p_left<=DAC_BITS; producer<=QUOTIENT;
    end
    QUOTIENT: begin
     p_dividend<=p_dividend<<1; p_quotient<=p_quotient_next;
     p_div_rem<=p_remainder_next; p_left<=p_left-1'b1;
     if(p_left==1) begin p_q<=p_quotient_next; p_r<=p_remainder_next; producer<=PREPARE; end
    end
    PREPARE: begin
     p_negative<={1'b0,p_r}-{1'b0,p_ticks};
     p_inc<=p_negative_slope ? -p_q:p_q;
     p_inc_carry<=p_negative_slope ? -p_q-1'b1:p_q+1'b1;
     producer<=PUSH;
    end
    PUSH: begin
     fifo_start[wr_ptr]<=p_start; fifo_ticks[wr_ptr]<=p_ticks;
     fifo_inc[wr_ptr]<=p_inc; fifo_inc_carry[wr_ptr]<=p_inc_carry;
     fifo_rem[wr_ptr]<=p_r; fifo_negative[wr_ptr]<=p_negative;
     fifo_error[wr_ptr]<={1'b0,p_r}-{1'b0,p_half};
     fifo_index[wr_ptr]<=p_index; fifo_last[wr_ptr]<=p_index==last_index;
     fifo_rise_last[wr_ptr]<=({1'b0,p_index}+1'b1)==rise_count;
     wr_ptr<=!wr_ptr; producer<=FETCH;
    end
   endcase

   if(state==2 && trigger_event) ignored<=ignored+1'b1;
   if(pop) begin
    run_index<=fifo_index[rd_ptr]; at_last<=fifo_last[rd_ptr]; at_rise_last<=fifo_rise_last[rd_ptr];
    if(start) begin state<=2; phase<=UP; end
    if(start_down) phase<=DOWN;
    if(advance && at_last && !gated) begin
     if(completed_loops!=16'hffff) completed_loops<=completed_loops+1'b1;
     round_loops<=round_loops+1'b1;
     final_loop<=!repeat_forever && (round_loops==repeat_limit);
    end
   end else if(early) begin
    phase<=DIVIDE;
   end else if(state==2) begin
    case(phase)
     UP,DOWN: if(remaining_last) begin
      if(up_end) phase<=HOLD;
     end
     DIVIDE: begin
      if(div_left==1) begin
       phase<=SETUP;
      end
     end
     SETUP: phase<=LINEAR;
    endcase
   end
  end
 end
endmodule
