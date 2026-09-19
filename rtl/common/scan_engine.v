`timescale 1ns/1ps
// One selected event produces one code. Between events the code is held.
module scan_engine #(parameter DAC_BITS=10)(
 input clk,rst,arm,disarm,abort_run,soft_trigger,external_trigger,valid,use_external,
 input [DAC_BITS-1:0] initial_code,safe_code,start_code,
 input signed [15:0] step_code,input [15:0] point_count,input loop_scan,
 output reg [1:0] state,output prepared,output reg [DAC_BITS-1:0] code,
 output reg [15:0] position,loops,output reg [31:0] ignored);
 localparam [DAC_BITS-1:0] MID_CODE=(1 << (DAC_BITS-1));
 reg [DAC_BITS-1:0] next_code;
 reg [15:0] next_index,last_index;
 wire event_tick=use_external ? external_trigger:soft_trigger;
 assign prepared=state==1 && valid;
 // Configuration is stable before ARM; no count subtraction in the event path.
 always @(posedge clk) last_index<=point_count-1'b1;
 always @(posedge clk) begin
  if(rst) begin state<=0;code<=MID_CODE;next_code<=MID_CODE;next_index<=0;position<=0;loops<=0;ignored<=0;end
  else if(abort_run) begin state<=0;code<=safe_code;next_index<=0;next_code<=start_code;position<=0;loops<=0;ignored<=0;end
  else if(disarm) begin state<=0;code<=initial_code;next_index<=0;next_code<=start_code;position<=0;loops<=0;ignored<=0;end
  else if(arm && valid) begin state<=1;code<=initial_code;next_index<=0;next_code<=start_code;position<=0;loops<=0;ignored<=0;end
  else if(event_tick && valid) begin
   if(state==1) begin
    code<=next_code;position<=next_index+1'b1;
    if(next_index==last_index) begin
     next_index<=0;next_code<=start_code;
     if(loops!=16'hffff)loops<=loops+1'b1;
     if(!loop_scan)state<=3;
    end else begin next_index<=next_index+1'b1;next_code<=next_code+step_code[DAC_BITS-1:0];end
   end else if(state==3 && ignored!=32'hffffffff)ignored<=ignored+1'b1;
  end
 end
endmodule
