`timescale 1ns/1ps
// Virtual trigger bank. Source changes are edge-free; pulse lengths are clock ticks.
// A single pulse train drives one selected input; static levels may use other inputs.
module trigger_debug #(parameter COUNT=2, CORE_HZ=200000000)(
 input clk,rst,configure,action,heartbeat,release_debug,halt,
 input [COUNT-1:0] requested_mask,
 input [2:0] input_index,input [1:0] operation,
 input [31:0] high_ticks,period_ticks,pulse_count,
 output reg [COUNT-1:0] mask,levels,rising,falling,
 output reg running,expired,output reg [2:0] active_input,
 output reg [31:0] emitted);
 reg [31:0] watchdog,left,high_length,low_length,remaining;
 reg infinite;
 always @(posedge clk) begin
  if(rst) begin
   mask<=0;levels<=0;rising<=0;falling<=0;running<=0;expired<=0;
   active_input<=0;emitted<=0;watchdog<=0;left<=0;high_length<=0;
   low_length<=0;remaining<=0;infinite<=0;
  end else begin
   rising<=0;falling<=0;
   if(release_debug) begin mask<=0;levels<=0;running<=0;watchdog<=0;end
   else if(halt) begin running<=0;levels<=0;end
   else if(configure) begin
    mask<=requested_mask;levels<=0;running<=0;expired<=0;emitted<=0;watchdog<=CORE_HZ*3;
   end else if(mask!=0 && !expired) begin
    if(heartbeat || action) watchdog<=CORE_HZ*3;
    else if(watchdog!=0) watchdog<=watchdog-1'b1;
    if(watchdog==0 && !heartbeat && !action) begin
     // Keep physical inputs masked until the controller has aborted all engines.
     expired<=1;running<=0;levels<=0;
    end else if(action) begin
     if(operation<2) begin
      if(operation==1 && !levels[input_index]) begin rising[input_index]<=1;emitted<=emitted+1'b1;end
      if(operation==0 && levels[input_index]) falling[input_index]<=1;
      levels[input_index]<=operation[0];
     end else begin
      active_input<=input_index;running<=1;levels[input_index]<=1;rising[input_index]<=1;
      emitted<=emitted+1'b1;left<=high_ticks-1'b1;
      high_length<=high_ticks;low_length<=period_ticks-high_ticks;
      remaining<=operation==2 ? 1:pulse_count;infinite<=operation==3 && pulse_count==0;
     end
    end else if(running) begin
     if(left!=0) left<=left-1'b1;
     else if(levels[active_input]) begin
      levels[active_input]<=0;falling[active_input]<=1;
      if(!infinite && remaining==1) running<=0;
      else begin left<=low_length-1'b1;if(!infinite) remaining<=remaining-1'b1;end
     end else begin
      levels[active_input]<=1;rising[active_input]<=1;left<=high_length-1'b1;emitted<=emitted+1'b1;
     end
    end
   end
  end
 end
endmodule
