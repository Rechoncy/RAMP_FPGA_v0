`timescale 1ns/1ps
// Descriptor: [83:74] target, [73:64] quotient, [63:32] remainder, [31:0] updates.
// RAM read latency: one clock. Pipeline settles >= 5 clocks before each update.
module sequence_engine(
 input clk,rst,arm,disarm,abort_run,soft_trigger,external_trigger,
 input config_valid,external_mode,input [9:0] initial_code,safe_code,
 input [15:0] divider,repeats,input [6:0] segment_count,
 output reg [5:0] read_index,input [83:0] descriptor,
 output reg [1:0] state,output reg prepared,
 output reg [9:0] code,output reg [5:0] segment_index,
 output reg [15:0] completed_loops,output reg [31:0] ignored_triggers);
 reg [83:0] current_desc,next_desc,first_desc,second_desc;
 reg [3:0] prepare_count;
 reg [2:0] fetch_count;
 reg [15:0] time_left;
 reg [31:0] updates_left,error_acc;
 reg direction_up;
 reg [32:0] sum_pipe,subtract_pipe;
 reg carry_pipe;
 reg [10:0] base_pipe,next_code_pipe;
 reg [31:0] error_pipe,next_error_pipe;
 reg [15:0] repeat_limit;
 reg last_loop,last_segment;
 reg tick_due,last_update;
 wire [31:0] n=current_desc[31:0], r=current_desc[63:32];
 wire [9:0] q=current_desc[73:64],target=current_desc[83:74];
 wire start_event=external_mode ? external_trigger:soft_trigger;
 always @(posedge clk) begin
  if(rst) begin
   state<=0; prepared<=0; code<=512; segment_index<=0; completed_loops<=0; ignored_triggers<=0;
   read_index<=0; current_desc<=0; next_desc<=0; first_desc<=0; second_desc<=0;
   prepare_count<=0; fetch_count<=0; time_left<=0; updates_left<=0; error_acc<=0;
   direction_up<=1; sum_pipe<=0; subtract_pipe<=0; carry_pipe<=0; base_pipe<=0;
   next_code_pipe<=0; error_pipe<=0; next_error_pipe<=0; repeat_limit<=0; last_loop<=0; last_segment<=0; tick_due<=0; last_update<=0;
  end else begin
   // Arithmetic is deliberately split over registers to meet the 5 ns clock budget.
   sum_pipe<={1'b0,error_acc}+{1'b0,r};
   carry_pipe<=sum_pipe>={1'b0,n};
   subtract_pipe<=sum_pipe-{1'b0,n};
   error_pipe<=sum_pipe[31:0];
   base_pipe<=direction_up ? {1'b0,code}+{1'b0,q}:{1'b0,code}-{1'b0,q};
   next_error_pipe<=carry_pipe ? subtract_pipe[31:0]:error_pipe;
   next_code_pipe<=direction_up ? base_pipe+carry_pipe:base_pipe-carry_pipe;
   repeat_limit<=repeats-1'b1;
   last_loop<=completed_loops>=repeat_limit;
   last_segment<=({1'b0,segment_index}+1'b1>=segment_count);
   tick_due<=time_left==1;
   last_update<=updates_left==1;
   if(state==2 && external_trigger) ignored_triggers<=ignored_triggers+1'b1;
   if(arm && config_valid && (state==0 || state==3)) begin
    state<=1; prepared<=0; prepare_count<=1; read_index<=0; fetch_count<=0;
    code<=initial_code; segment_index<=0; completed_loops<=0; ignored_triggers<=0; time_left<=0; tick_due<=0;
   end else if(prepare_count!=0) begin
    prepare_count<=prepare_count+1'b1;
    if(prepare_count==3) begin
     current_desc<=descriptor; first_desc<=descriptor; read_index<=segment_count>1 ? 1:0;
    end
    if(prepare_count==6) begin
     next_desc<=descriptor; second_desc<=descriptor; prepare_count<=0; prepared<=1;
    end
   end
   if(fetch_count!=0) begin
    fetch_count<=fetch_count-1'b1;
    if(fetch_count==1) next_desc<=descriptor;
   end
   if(state==1 && prepared && start_event) begin
    state<=2; time_left<=divider-1'b1; tick_due<=0; updates_left<=current_desc[31:0];
    error_acc<=0; direction_up<=current_desc[83:74]>=initial_code;
   end
   if(state==2) begin
    if(!tick_due) time_left<=time_left-1'b1;
    else begin
     time_left<=divider-1'b1;
     code<=last_update ? target:next_code_pipe[9:0];
     error_acc<=next_error_pipe; updates_left<=updates_left-1'b1;
     if(last_update) begin
      error_acc<=0;
      if(last_segment) begin
       completed_loops<=completed_loops+1'b1;
       if(last_loop) begin state<=3; prepared<=0; end
       else begin
        segment_index<=0; current_desc<=first_desc; next_desc<=second_desc;
        updates_left<=first_desc[31:0]; direction_up<=first_desc[83:74]>=target;
       end
      end else begin
       segment_index<=segment_index+1'b1; current_desc<=next_desc;
       updates_left<=next_desc[31:0]; direction_up<=next_desc[83:74]>=target;
       read_index<=({1'b0,segment_index}+2<segment_count) ? segment_index+2:0;
       fetch_count<=3;
      end
     end
    end
   end
   if(disarm && state!=2) begin state<=0; prepared<=0; prepare_count<=0; code<=initial_code; time_left<=0; tick_due<=0; end
   if(abort_run) begin state<=0; prepared<=0; prepare_count<=0; fetch_count<=0; code<=safe_code; time_left<=0; tick_due<=0; end
  end
 end
endmodule
