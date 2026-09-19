`timescale 1ns/1ps
// Event steps own the existing fixed/gate engines. No host timers and no edge queue.
module program_sequencer #(parameter DAC_BITS=10, TRIGGER_COUNT=2, ADDR_BITS=13)(
 input clk,rst,arm,disarm,abort_run,active_valid,
 input [6:0] step_count,input [191:0] step_word,
 input [TRIGGER_COUNT-1:0] rising,falling,
 input [1:0] engine_state,input engine_prepared,
 output reg [5:0] step_index,output reg engine_arm,
 output start_fixed,gate_rising,gate_falling,
 output reg [1:0] state,output prepared,
 output reg [2:0] phase,output reg [15:0] loops,output reg [31:0] ignored,
 output reg [1:0] kind,output reg [2:0] source,output reg edge_falling,
 output reg [ADDR_BITS-1:0] base,output reg [ADDR_BITS:0] count,rise_count,
 output reg [DAC_BITS-1:0] target,output reg [31:0] fall_ticks,
 output reg [15:0] repeats,output reg linear_mode,output reg gate_mode);
 localparam LOAD0=0,WAIT_EDGE=1,WAIT_DELAY=2,PLAY=3,LOAD1=4,LOAD2=5,PREPARE=6,LATCH=7;
 reg [31:0] delay_ticks,delay_left;
 reg seen_running;
 reg automatic_step;
 // The active bank/address is stable during playback. Continuously register
 // metadata, without a reset on RAM output registers. LOAD stages below wait
 // for these values before arming either engine. This keeps asynchronous clock
 // reset signals away from BRAM control pins after register absorption.
 always @(posedge clk) begin
  kind<=step_word[9:8];source<=step_word[18:16];edge_falling<=step_word[24];
  base<=step_word[32+:ADDR_BITS];count<=step_word[48+:ADDR_BITS+1];rise_count<=step_word[64+:ADDR_BITS+1];target<=step_word[80+:DAC_BITS];
  fall_ticks<=step_word[127:96];delay_ticks<=step_word[159:128];repeats<=step_word[175:160];
  linear_mode<=step_word[176];
  gate_mode<=step_word[9:8]==1;automatic_step<=step_word[9:8]==2;
 end
 wire edge_event=edge_falling ? falling[source]:rising[source];
 // PREPARE is the readiness barrier. Once a step enters WAIT_EDGE/WAIT_DELAY,
 // its armed player stays prepared until this sequencer starts or cancels it.
 // Do not feed the multi-player state mux back into every trigger/data CE.
 wire ready_edge=phase==WAIT_EDGE;
 assign prepared=state==1 && ready_edge;
 assign start_fixed=!gate_mode && ((ready_edge && edge_event) || (phase==WAIT_DELAY && delay_left==0));
 assign gate_rising=gate_mode && (ready_edge || phase==PLAY) && rising[source];
 assign gate_falling=gate_mode && phase==PLAY && falling[source];
 wire start=gate_mode ? ready_edge && rising[source]:start_fixed;
 wire finish=phase==PLAY && seen_running && engine_state!=2;
 always @(posedge clk) begin
  if(rst) begin
   step_index<=0;engine_arm<=0;state<=0;phase<=LOAD0;loops<=0;ignored<=0;
   delay_left<=0;seen_running<=0;
  end else begin
   engine_arm<=0;
   if(arm || disarm || abort_run) begin
    state<=arm && active_valid ? 1:0;phase<=LOAD0;step_index<=0;loops<=0;ignored<=0;seen_running<=0;
   end else if(state!=0) begin
    if(phase!=WAIT_EDGE && edge_event && !automatic_step && ignored!=32'hffffffff) ignored<=ignored+1'b1;
    case(phase)
     LOAD0: phase<=LOAD1;
     LOAD1: phase<=LOAD2;
     LOAD2: begin
      phase<=LATCH;seen_running<=0;
     end
     LATCH: begin phase<=PREPARE;engine_arm<=1;end
     PREPARE: if(!engine_arm && engine_prepared) begin
      phase<=automatic_step ? WAIT_DELAY:WAIT_EDGE;state<=automatic_step ? 2:1;delay_left<=delay_ticks;
     end
     WAIT_EDGE,WAIT_DELAY: begin
      if(phase==WAIT_DELAY && delay_left!=0) delay_left<=delay_left-1'b1;
      if(start) begin phase<=PLAY;state<=2;seen_running<=0;end
     end
     PLAY: begin
      if(engine_state==2)seen_running<=1;
      if(finish) begin
       if(step_index+1'b1==step_count) begin
        step_index<=0;if(loops!=65535)loops<=loops+1'b1;
       end else step_index<=step_index+1'b1;
       phase<=LOAD0;state<=1;seen_running<=0;
      end
     end
    endcase
   end
  end
 end
endmodule
