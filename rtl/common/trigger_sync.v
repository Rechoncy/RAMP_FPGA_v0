`timescale 1ns/1ps
// Suppress edges until the initial level has crossed the synchronizer.
module trigger_sync(input clk,rst,signal_in,output pulse,fall_pulse);
 (* ASYNC_REG="TRUE" *) reg [1:0] sync_ff;
 reg previous;
 reg [2:0] valid;
 assign pulse=valid[2] && sync_ff[1] && !previous;
 assign fall_pulse=valid[2] && !sync_ff[1] && previous;
 always @(posedge clk) begin
  if(rst) begin sync_ff<=0; previous<=0; valid<=0; end
  else begin sync_ff<={sync_ff[0],signal_in}; previous<=sync_ff[1];
   valid<={valid[1:0],1'b1};
  end
 end
endmodule
