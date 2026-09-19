`timescale 1ns/1ps
// Edge-gated RLE player. Early falling edges capture the current code and
// calculate a bounded-time integer linear return (DAC_BITS+1 setup clocks).
module gate_engine #(parameter DAC_BITS=10)(
 input clk,rst,arm,disarm,abort_run,rising,falling,active_valid,
 input [DAC_BITS-1:0] initial_code,safe_code,target_code,
 input [13:0] run_count,rise_count,input [31:0] fall_ticks,
 output read_en,output reg [12:0] read_index,input [DAC_BITS+31:0] descriptor,
 output reg [1:0] state,output prepared,output reg [DAC_BITS-1:0] code,
 output reg [12:0] run_index,output reg [15:0] completed_loops,
 output reg [31:0] ignored,output reg [2:0] phase);
 localparam UP=1,HOLD=2,DOWN=3,DIVIDE=4,LINEAR=5,SETUP=6;
 reg [DAC_BITS+31:0] fifo[0:3];
 reg [1:0] rd_ptr,wr_ptr;
 reg [2:0] occupancy;
 reg inflight;
 reg [31:0] remaining;
 reg remaining_last;
 reg [12:0] last_index,rise_penultimate,last_penultimate;
 reg at_rise_last,at_last;
 always @(posedge clk) begin
  last_index<=run_count-1'b1;
  rise_penultimate<=rise_count-2'd2; last_penultimate<=run_count-2'd2;
 end
 wire [DAC_BITS+31:0] head=fifo[rd_ptr];
 assign prepared=state==1 && occupancy>=3;
 wire start=prepared && rising && !falling;
 wire up_end=phase==UP && remaining_last && at_rise_last;
 wire start_down=state==2 && falling && (phase==HOLD || up_end);
 wire early=state==2 && falling && phase==UP && !up_end;
 wire advance=state==2 && remaining_last && !early &&
                ((phase==UP && !up_end) || (phase==DOWN && !at_last));
 wire pop=start || start_down || advance;
 wire finish=state==2 && ((phase==DOWN && remaining_last && at_last) ||
                          (phase==LINEAR && remaining_last));
 assign read_en=(state==1 || (state==2 && phase<=DOWN)) && !arm && !disarm && !abort_run &&
                ({1'b0,occupancy}+inflight<4);
 reg [DAC_BITS-1:0] dividend,quotient,step_q;
 reg [31:0] step_r;
 reg [32:0] error_acc,negative_step;
 reg [DAC_BITS:0] div_rem;
 reg [DAC_BITS-1:0] increment,increment_carry;
 reg [4:0] div_left;
 reg direction;
 wire [DAC_BITS:0] trial={div_rem[DAC_BITS-1:0],dividend[DAC_BITS-1]};
 wire [DAC_BITS+1:0] difference={1'b0,trial}-{1'b0,fall_ticks[DAC_BITS:0]};
 wire subtract=!(|fall_ticks[31:DAC_BITS+1]) && !difference[DAC_BITS+1];
 wire [DAC_BITS:0] remainder_next=subtract ? difference[DAC_BITS:0]:trial;
 wire [DAC_BITS-1:0] quotient_next={quotient[DAC_BITS-2:0],subtract};
 // Signed error looks one sample ahead. The carry is a registered sign bit,
 // avoiding an add -> compare -> DAC add chain within a single 5 ns cycle.
 wire carry=!error_acc[32];
 always @(posedge clk) begin
  if(rst) begin
   state<=0; phase<=0; code<=(1 << (DAC_BITS-1)); read_index<=0; run_index<=0;
   completed_loops<=0; ignored<=0; rd_ptr<=0; wr_ptr<=0; occupancy<=0; inflight<=0; remaining<=0;
   dividend<=0; quotient<=0; step_q<=0; step_r<=0; error_acc<=0; div_rem<=0; div_left<=0; direction<=0;
   negative_step<=0; increment<=0; increment_carry<=0;
   at_rise_last<=0; at_last<=0;
   remaining_last<=0;
  end else if(arm || disarm || abort_run || finish) begin
   state<=!abort_run && !disarm && (finish || (arm && active_valid)) ? 1:0; phase<=0;
   code<=abort_run ? safe_code:initial_code;
   read_index<=0; run_index<=0; rd_ptr<=0; wr_ptr<=0; occupancy<=0; inflight<=0; remaining<=0;
   at_rise_last<=0; at_last<=0;
   remaining_last<=0;
   if(!finish || abort_run || disarm || arm) begin completed_loops<=0; ignored<=0; end
   else if(completed_loops!=65535) completed_loops<=completed_loops+1'b1;
  end else begin
   inflight<=read_en;
   if(read_en) read_index<=read_index==last_index ? 13'd0:read_index+1'b1;
   if(inflight) begin fifo[wr_ptr]<=descriptor; wr_ptr<=wr_ptr+1'b1; end
   if(pop) rd_ptr<=rd_ptr+1'b1;
   case({inflight,pop})
    2'b10:occupancy<=occupancy+1'b1;
    2'b01:occupancy<=occupancy-1'b1;
   endcase
   if(state==2 && rising) ignored<=ignored+1'b1;
   if(start) begin
    state<=2; phase<=UP; run_index<=0; code<=head[DAC_BITS+31:32]; remaining<=head[31:0];
    at_rise_last<=rise_count==1; at_last<=0;
    remaining_last<=head[31:0]==1;
   end else if(early) begin
    phase<=DIVIDE; dividend<=code>=initial_code ? code-initial_code:initial_code-code;
    direction<=code>=initial_code; quotient<=0; div_rem<=0; div_left<=DAC_BITS;
   end else if(start_down) begin
    phase<=DOWN; run_index<=rise_count[12:0]; code<=head[DAC_BITS+31:32]; remaining<=head[31:0];
    at_rise_last<=0; at_last<=rise_count[12:0]==last_index;
    remaining_last<=head[31:0]==1;
   end else if(state==2) begin
    case(phase)
     UP,DOWN: if(remaining_last) begin
      if(up_end) begin phase<=HOLD; code<=target_code; end
      else if(advance) begin
       run_index<=run_index+1'b1; code<=head[DAC_BITS+31:32]; remaining<=head[31:0];
       at_rise_last<=run_index==rise_penultimate; at_last<=run_index==last_penultimate;
       remaining_last<=head[31:0]==1;
      end
     end else begin remaining<=remaining-1'b1; remaining_last<=remaining==2; end
     DIVIDE: begin
      dividend<=dividend<<1; quotient<=quotient_next; div_rem<=remainder_next; div_left<=div_left-1'b1;
      if(div_left==1) begin
       step_q<=quotient_next; step_r<=remainder_next;
       remaining<=fall_ticks; phase<=SETUP;
       remaining_last<=fall_ticks==1;
      end
     end
     SETUP: begin
      error_acc<={1'b0,step_r}-{1'b0,fall_ticks};
      negative_step<={1'b0,step_r}-{1'b0,fall_ticks};
      increment<=direction ? -step_q:step_q;
      increment_carry<=direction ? -step_q-1'b1:step_q+1'b1;
      phase<=LINEAR;
     end
     LINEAR: begin
      remaining<=remaining-1'b1;
      remaining_last<=remaining==2;
      error_acc<=error_acc+(carry ? negative_step:{1'b0,step_r});
      code<=code+(carry ? increment_carry:increment);
     end
    endcase
   end
  end
 end
endmodule
