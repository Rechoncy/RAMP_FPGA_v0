`timescale 1ns/1ps
module ramp_controller #(parameter CORE_HZ=200000000, DAC_COUNT=2, TRIGGER_COUNT=2, DAC_BITS=10, RUN_ADDR_BITS=13, CACHE_ENABLED=1, LEGACY_ENABLED=1,
 USB_FIFO_ENABLED=0,
 parameter [255:0] BOOT_CONFIG=0, parameter BOOT_MEMORY_FILE="", parameter UART_AUTOBAUD=0)(
 input clk,rst,input [255:0] request,input request_valid,
 output reg [239:0] response,output reg response_valid,input response_ready,
 input [TRIGGER_COUNT-1:0] trigger_rise,trigger_fall,
 output [DAC_COUNT*DAC_BITS-1:0] dac_codes, output [5:0] monitor);
 localparam ADDR_BITS=(RUN_ADDR_BITS>13 ? RUN_ADDR_BITS:13);
 localparam RUN_CAPACITY=(1 << RUN_ADDR_BITS);
 assign monitor={active_gate,active_external,active_valid,prepared,run_state};
 localparam [DAC_BITS-1:0] MID_CODE=(1 << (DAC_BITS-1));
 localparam [15:0] MAX_CODE=(1 << DAC_BITS)-1;
 wire ramp_trigger=trigger_rise[0];
 wire fm_trigger;
 generate if(TRIGGER_COUNT>1) assign fm_trigger=trigger_rise[1];
 else assign fm_trigger=1'b0; endgenerate
 reg [239:0] previous_request,cached_response;
 reg [255:0] incoming;
 reg [7:0] equal_chunks;
 integer chunk;
 reg cache_valid;
 reg [4:0] command_state;
 reg [1:0] batch_left;
 reg [191:0] batch_payload;
 integer batch_i;
 reg [16:0] batch_end;
 reg [4:0] batch_checks;
 reg batch_fields_valid;
 // Incoming is captured in state 0; states 5 and 6 settle these two stages
 // before state 7 admits the complete batch. Separate add/compare/admission
 // paths without changing upload or playback cycles.
 always @(posedge clk) begin
  if(rst)begin batch_end<=0;batch_checks<=0;batch_fields_valid<=0;end
  else begin
   batch_end<={1'b0,payload[15:0]}+{9'b0,payload[23:16]};
   batch_checks[0]<=payload[23:16]>=1 && payload[23:16]<=3 &&
                    payload[31:24]==0 && payload[191:176]==0;
   batch_checks[1]<=payload[15:0]==sample_loaded;
   for(batch_i=0;batch_i<3;batch_i=batch_i+1)begin
    if(batch_i<payload[23:16])
     batch_checks[2+batch_i]<=payload[32+batch_i*48+:16]<=MAX_CODE &&
       payload[48+batch_i*48+:32]!=0 &&
       (!pending_linear || pending_program || payload[48+batch_i*48+:32]>=32);
    else batch_checks[2+batch_i]<=payload[32+batch_i*48+:48]==0;
   end
   batch_fields_valid<=(&batch_checks) && batch_end<=pending_runs;
  end
 end
 reg [7:0] error_code;
 reg [239:0] work;
 reg [7:0] cmd;
 reg active_bank,pending_valid,active_valid;
 reg [6:0] loaded,pending_count,active_count;
 reg [DAC_BITS-1:0] pending_initial,active_initial,pending_safe,active_safe;
 reg [9:0] previous_target;
 reg [15:0] pending_divider,active_divider,pending_repeats,active_repeats;
 reg pending_external,active_external;
 reg [31:0] pending_id,active_id;
 reg [83:0] memory [0:127];
 reg [83:0] descriptor;
 reg memory_write_en;
 reg [6:0] memory_write_address;
 reg [83:0] memory_write_data;
 reg [9:0] legacy_fm_code;
 reg active_samples,pending_samples;
 reg active_linear,pending_linear;
 reg active_gate,pending_gate;
 reg active_program,pending_program;
 reg active_scan,pending_scan,active_scan_loop,pending_scan_loop;
 reg [DAC_BITS-1:0] active_scan_start,pending_scan_start;
 reg signed [15:0] active_scan_step,pending_scan_step;
 reg [15:0] active_scan_points,pending_scan_points;
 wire [1:0] scan_state;
 wire scan_prepared;
 wire [DAC_BITS-1:0] scan_code;
 wire [15:0] scan_position,scan_loops;
 wire [31:0] scan_ignored;
 reg signed [32:0] scan_delta,scan_last;
 reg [4:0] scan_checks;
 always @(posedge clk) begin
  scan_delta<=$signed(payload[63:48]) * ($signed({1'b0,payload[79:64]})-17'sd1);
  scan_last<=scan_delta+$signed({1'b0,payload[47:32]});
  scan_checks[0]<=payload[15:0]<=MAX_CODE && payload[31:16]<=MAX_CODE && payload[47:32]<=MAX_CODE;
  scan_checks[1]<=payload[79:64]!=0 && $signed(payload[63:48])<= $signed({1'b0,MAX_CODE}) && $signed(payload[63:48])>= -$signed({1'b0,MAX_CODE});
  scan_checks[2]<=payload[119:112]<DAC_COUNT && payload[127:120]<=7 && (!payload[128] || payload[127:120]<TRIGGER_COUNT);
  scan_checks[3]<=payload[135:128]<=7 && payload[191:136]==0;
  scan_checks[4]<=scan_last>=0 && scan_last<=$signed({1'b0,MAX_CODE});
 end
 reg [6:0] active_steps,pending_steps,steps_loaded;
 reg [191:0] step_memory [0:127];
 reg [191:0] step_descriptor,step_write_data;
 reg [191:0] verify_step_descriptor;
 reg step_write_en;
 reg [6:0] step_write_address;
 wire [5:0] program_step;
 wire program_arm,program_start,program_rising,program_falling,program_prepared;
 wire [1:0] program_state,program_kind;
 wire [2:0] program_phase,program_source;
 wire program_edge;
 wire program_linear,program_gate;
 wire [ADDR_BITS-1:0] program_base;
 wire [ADDR_BITS:0] program_count,program_rise;
 wire [DAC_BITS-1:0] program_target;
 wire [31:0] program_fall,program_ignored;
 wire [15:0] program_repeats,program_loops;
 wire use_gate=active_program ? program_gate:active_gate;
 wire use_linear=active_program ? program_linear:active_linear;
 // COMMIT checks descriptors in the inactive bank through the existing read
 // ports. No per-descriptor mode bit or extra waveform storage is required.
 reg [2:0] verify_phase;
 reg [5:0] verify_step;
 reg [ADDR_BITS:0] verify_index,verify_end,verify_rise_end;
 reg verify_gate;
 reg [DAC_BITS-1:0] verify_target;
 reg [31:0] verify_fall_ticks,verify_fall_sum;
 reg [DAC_BITS+31:0] verify_descriptor;
 reg [32:0] verify_next_sum;
 reg [3:0] verify_errors;
 reg verify_last,verify_returning,verify_last_step;
 // COMMIT is an offline operation. Pipeline its full-table validation so a
 // 33-bit sum, comparison and bank-control enable never form one 5 ns path.
 always @(posedge clk) begin
  verify_descriptor<=sample_descriptor;
  verify_next_sum<={1'b0,verify_fall_sum}+{1'b0,verify_descriptor[31:0]};
  verify_errors[0]<=verify_descriptor[31:0]<32;
  verify_errors[1]<=verify_gate && verify_index+1'b1==verify_rise_end && verify_descriptor[32+:DAC_BITS]!=verify_target;
  verify_errors[2]<=verify_gate && verify_index>=verify_rise_end && verify_next_sum>{1'b0,verify_fall_ticks};
  verify_errors[3]<=verify_gate && verify_index+1'b1==verify_end &&
                    (verify_descriptor[32+:DAC_BITS]!=pending_initial || verify_next_sum!={1'b0,verify_fall_ticks});
  verify_last<=verify_index+1'b1==verify_end;
  verify_returning<=verify_index>=verify_rise_end;
  verify_last_step<={1'b0,verify_step}+1'b1==pending_steps;
 end
 initial if(BOOT_CONFIG[4]) $readmemh({BOOT_MEMORY_FILE,".steps"},step_memory);
 always @(posedge clk) begin
  step_descriptor<=step_memory[{command_state==10 ? !active_bank:active_bank,command_state==10 ? verify_step:program_step}];
  verify_step_descriptor<=step_descriptor;
  if(step_write_en)step_memory[step_write_address]<=step_write_data;
 end
 // Pending data settles before the next UART command reaches dispatch.
 // Register validation to keep the large bank-commit enable below 5 ns.
 reg commit_ready,commit_loop_valid,run_fields_valid;
 reg [3:0] begin_sample_checks,begin_gate_checks;
 reg begin_sample_valid,begin_gate_valid;
 always @(posedge clk) begin
  if(rst) begin commit_ready<=0; commit_loop_valid<=0; run_fields_valid<=0;
   begin_sample_checks<=0;begin_gate_checks<=0;begin_sample_valid<=0;begin_gate_valid<=0;end
  else begin
   // Two validation stages settle during command capture/cache dispatch. This
   // avoids routing every incoming field into a high-fanout configuration enable.
   begin_sample_checks[0]<=payload[15:0]<=MAX_CODE && payload[31:16]<=MAX_CODE;
   begin_sample_checks[1]<=payload[47:32]!=0 && payload[47:32]<=RUN_CAPACITY;
   begin_sample_checks[2]<=payload[103:96]<=1 && payload[111:104]<DAC_COUNT && payload[135:128]<=1;
   begin_sample_checks[3]<=(!payload[128] || (payload[119:112]<=7 && payload[127:120]<=1)) &&
                          (!payload[96] || (payload[128] ? payload[119:112]:payload[111:104])<TRIGGER_COUNT);
   begin_gate_checks[0]<=payload[15:0]<=MAX_CODE && payload[31:16]<=MAX_CODE && payload[127:112]<=MAX_CODE;
   begin_gate_checks[1]<=payload[47:32]>=2 && payload[47:32]<=RUN_CAPACITY && payload[63:48]!=0 && payload[63:48]<payload[47:32];
   begin_gate_checks[2]<=payload[103:96]<DAC_COUNT && payload[111:104]<TRIGGER_COUNT;
   begin_gate_checks[3]<=payload[159:128]!=0 && payload[159:128]<=CORE_HZ;
   begin_sample_valid<=&begin_sample_checks;begin_gate_valid<=&begin_gate_checks;
   commit_ready<=pending_valid && (pending_samples ? sample_loaded==pending_runs && (!pending_program || steps_loaded==pending_steps):loaded==pending_count);
   commit_loop_valid<=pending_samples || pending_repeats<=1 || previous_target==pending_initial;
   run_fields_valid<=payload[15:0]==sample_loaded && sample_loaded<pending_runs &&
                     payload[31:16]<=MAX_CODE && payload[63:32]!=0 &&
                     (!pending_linear || pending_program || payload[63:32]>=32);
  end
 end
 reg [2:0] program_checks;
 reg [7:0] step_checks;
 reg program_fields_valid,step_fields_valid;
 always @(posedge clk) begin
  program_checks[0]<=payload[15:0]<=MAX_CODE && payload[31:16]<=MAX_CODE;
  program_checks[1]<=payload[47:32]!=0 && payload[47:32]<=RUN_CAPACITY && payload[63:48]!=0 && payload[63:48]<=64;
  program_checks[2]<=payload[103:96]<DAC_COUNT && payload[191:104]==0;
  program_fields_valid<=&program_checks;
  step_checks[0]<=payload[7:0]==steps_loaded && steps_loaded<pending_steps;
  step_checks[1]<=payload[15:8]<=2 && payload[23:16]<TRIGGER_COUNT && payload[31:24]<=1;
  step_checks[2]<=payload[63:48]!=0 && ({1'b0,payload[47:32]}+{1'b0,payload[63:48]})<={1'b0,pending_runs};
  step_checks[3]<=payload[95:80]<=MAX_CODE && payload[175:160]!=0 &&
                  (pending_linear ? payload[191:176]<=1:payload[191:176]==0);
  step_checks[4]<=payload[15:8]!=1 || (payload[79:64]!=0 && payload[79:64]<payload[63:48]);
  step_checks[5]<=payload[15:8]!=1 || (payload[127:96]!=0 && payload[127:96]<=CORE_HZ);
  step_checks[6]<=payload[15:8]!=1 || (payload[175:160]==1 && payload[31:24]==0);
  step_checks[7]<=steps_loaded!=0 || payload[15:8]!=2;
  step_fields_valid<=&step_checks;
 end
 reg [ADDR_BITS:0] active_rise,pending_rise;
 reg [31:0] active_fall_ticks,pending_fall_ticks;
 reg [DAC_BITS-1:0] active_target,pending_target;
 reg [2:0] active_channel,pending_channel,active_trigger,pending_trigger;
 reg active_falling,pending_falling;
 reg [ADDR_BITS:0] sample_loaded,pending_runs,active_runs;
 reg [DAC_BITS+31:0] sample_memory [0:2*RUN_CAPACITY-1];
 // The configuration bitstream initializes bank zero; UART uploads still use
 // the ordinary double-buffered RAM. Never restore boot metadata after a
 // clock reset, since a later UART upload may have overwritten that bank.
 initial if(BOOT_CONFIG[0]) $readmemh(BOOT_MEMORY_FILE,sample_memory);
 reg boot_available=BOOT_CONFIG[0];
 reg active_boot;
 always @(posedge clk) if(!rst) boot_available<=0;
 reg [DAC_BITS+31:0] sample_descriptor;
 reg [DAC_BITS+31:0] sample_write_data;
 reg sample_write_en;
 reg [RUN_ADDR_BITS:0] sample_write_address;
 wire sample_read_en,fixed_read_en,gate_read_en;
 wire [ADDR_BITS-1:0] fixed_read_index,gate_read_index,fixed_index,gate_index;
 wire [1:0] fixed_state,gate_state;
 wire fixed_prepared,gate_prepared;
 wire [DAC_BITS-1:0] fixed_code,gate_code;
 wire [15:0] fixed_loops,gate_loops;
 wire [31:0] fixed_ignored,gate_ignored;
 wire [2:0] gate_phase;
 wire linear_read_en,linear_prepared;
 wire [ADDR_BITS-1:0] linear_read_index,linear_index;
 wire [1:0] linear_state;
 wire [DAC_BITS-1:0] linear_code;
 wire [15:0] linear_loops;
 wire [31:0] linear_ignored;
 wire [2:0] linear_phase;
 wire [1:0] player_state=use_linear ? linear_state:use_gate ? gate_state:fixed_state;
 wire player_prepared=use_linear ? linear_prepared:use_gate ? gate_prepared:fixed_prepared;
 wire [2:0] selected_gate_phase=use_linear ? linear_phase:gate_phase;
 wire [ADDR_BITS-1:0] sample_read_index,sample_index;
 wire [1:0] sample_state,legacy_state;
 wire sample_prepared,legacy_prepared;
 wire [DAC_BITS-1:0] sample_code;
 wire [9:0] legacy_code;
 wire [15:0] sample_loops,legacy_loops;
 wire [31:0] sample_ignored,legacy_ignored;
 wire [DAC_BITS-1:0] selected_code=!active_valid ? MID_CODE:active_samples ? sample_code:legacy_code;
 genvar channel;
 generate for(channel=0;channel<DAC_COUNT;channel=channel+1) begin : outputs
  if(channel==0) assign dac_codes[channel*DAC_BITS+:DAC_BITS]=!active_valid ? MID_CODE:active_samples ? (active_channel==channel ? sample_code:MID_CODE):legacy_code;
  else if(channel==1) assign dac_codes[channel*DAC_BITS+:DAC_BITS]=!active_valid ? MID_CODE:active_samples ? (active_channel==channel ? sample_code:MID_CODE):legacy_fm_code;
  else assign dac_codes[channel*DAC_BITS+:DAC_BITS]=(active_samples && active_channel==channel) ? sample_code:MID_CODE;
 end endgenerate
 wire selected_trigger=active_falling ? trigger_fall[active_trigger]:trigger_rise[active_trigger];
 wire [5:0] read_index,segment_index;
 wire [1:0] run_state;
 reg configuration_busy;
 // Command capture/cache dispatch already takes several clocks. Register the
 // composite player status before it drives every pending-configuration CE.
 // External edges only move ARMED <-> RUNNING, both busy; new ARM/ABORT/COMMIT
 // commands cannot overlap this dispatch pipeline.
 always @(posedge clk) if(rst)configuration_busy<=0;else configuration_busy<=run_state==1 || run_state==2;
 wire prepared;
 wire [15:0] completed_loops;
 wire [31:0] ignored;
 reg arm,disarm,abort_run,soft_trigger;
 reg [31:0] divisor;
 reg [9:0] division_rem,division_next;
 reg division_can_subtract;
 reg [9:0] quotient,upload_target;
 reg [9:0] fm_initial,fm_step,pending_fm_initial,pending_fm_step;
 reg [10:0] fm_points,pending_fm_points,fm_counter;
 wire [10:0] fm_sum={1'b0,legacy_fm_code}+{1'b0,fm_step};
 wire [191:0] payload=incoming[239:48];
 assign run_state=active_samples ? sample_state:legacy_state;
 assign prepared=active_samples ? sample_prepared:legacy_prepared;
 assign completed_loops=active_samples ? sample_loops:legacy_loops;
 assign ignored=active_samples ? sample_ignored:legacy_ignored;
 generate if(LEGACY_ENABLED) begin: legacy_player
 sequence_engine u_engine(clk,rst,arm && !active_samples,disarm,abort_run,soft_trigger,ramp_trigger,
  active_valid && !active_samples,active_external,active_initial[9:0],active_safe[9:0],active_divider,active_repeats,active_count,
  read_index,descriptor,legacy_state,legacy_prepared,legacy_code,segment_index,legacy_loops,legacy_ignored);
 end else begin: no_legacy_player
  assign read_index=0;assign legacy_state=0;assign legacy_prepared=0;assign legacy_code=0;
  assign segment_index=0;assign legacy_loops=0;assign legacy_ignored=0;
 end endgenerate
 assign sample_read_en=use_linear ? linear_read_en:use_gate ? gate_read_en:fixed_read_en;
 assign sample_read_index=use_linear ? linear_read_index:use_gate ? gate_read_index:fixed_read_index;
 assign sample_state=active_scan ? scan_state:active_program ? program_state:player_state;
 assign sample_prepared=active_scan ? scan_prepared:active_program ? program_prepared:player_prepared;
 assign sample_code=active_scan ? scan_code:active_program && (program_state!=2 || program_phase!=3) ? (run_state==0 && program_aborted ? active_safe:active_initial):use_linear ? linear_code:use_gate ? gate_code:fixed_code;
 assign sample_index=use_linear ? linear_index:use_gate ? gate_index:fixed_index;
 assign sample_loops=active_scan ? scan_loops:active_program ? program_loops:use_linear ? linear_loops:use_gate ? gate_loops:fixed_loops;
 assign sample_ignored=active_scan ? scan_ignored:active_program ? program_ignored:use_linear ? linear_ignored:use_gate ? gate_ignored:fixed_ignored;
 reg [ADDR_BITS:0] play_count,play_rise;
 reg [DAC_BITS-1:0] play_target;
 reg [31:0] play_fall;
 reg [15:0] play_repeats;
 reg fixed_selected,gate_selected,linear_selected;
 reg [RUN_ADDR_BITS-1:0] play_base;
 // Register metadata selection before arming; keep selector LUTs off arithmetic feedback paths.
 always @(posedge clk) begin
  if(rst)begin
   // A single reset edge must also prepare metadata before the first boot
   // ARM: speculative read-index arithmetic starts on its following clock.
   play_count<=BOOT_CONFIG[0] && boot_available ? BOOT_CONFIG[96+:ADDR_BITS+1]:14'd0;
   play_rise<=BOOT_CONFIG[0] && boot_available ? BOOT_CONFIG[112+:ADDR_BITS+1]:14'd0;
   play_target<=BOOT_CONFIG[0] && boot_available ? BOOT_CONFIG[160+:DAC_BITS]:MID_CODE;
   play_fall<=BOOT_CONFIG[0] && boot_available ? BOOT_CONFIG[159:128]:32'd1;
   play_repeats<=BOOT_CONFIG[0] && boot_available ? BOOT_CONFIG[63:48]:16'd1;
   play_base<=0;
   // Boot can issue ARM on the first clock after reset. Seed standalone
   // selection from the same boot bits; sequence steps have a LOAD barrier.
   fixed_selected<=BOOT_CONFIG[0] && boot_available && !BOOT_CONFIG[1] && !BOOT_CONFIG[4] && !BOOT_CONFIG[5] && !BOOT_CONFIG[7];
   gate_selected<=BOOT_CONFIG[0] && boot_available && BOOT_CONFIG[1] && !BOOT_CONFIG[4] && !BOOT_CONFIG[7];
   linear_selected<=BOOT_CONFIG[0] && boot_available && BOOT_CONFIG[7] && !BOOT_CONFIG[4] && !BOOT_CONFIG[5];
  end else begin
   play_count<=active_program ? program_count:active_runs;
   play_rise<=active_program ? program_rise:active_rise;
   play_target<=active_program ? program_target:active_target;
   play_fall<=active_program ? program_fall:active_fall_ticks;
   play_repeats<=active_program ? program_repeats:active_repeats;
   play_base<=active_program ? program_base[RUN_ADDR_BITS-1:0]:{RUN_ADDR_BITS{1'b0}};
   fixed_selected<=active_samples && !active_scan && !use_gate && !use_linear;
   gate_selected<=use_gate && !use_linear;
   linear_selected<=active_samples && !active_scan && use_linear;
  end
 end
 reg program_aborted;
 always @(posedge clk) if(rst || arm || disarm)program_aborted<=0;else if(abort_run)program_aborted<=1;
 program_sequencer #(.DAC_BITS(DAC_BITS),.TRIGGER_COUNT(TRIGGER_COUNT),.ADDR_BITS(ADDR_BITS)) u_program(
  clk,rst,arm && active_program,disarm,abort_run,active_valid && active_program,active_steps,step_descriptor,
  trigger_rise,trigger_fall,player_state,player_prepared,
  program_step,program_arm,program_start,program_rising,program_falling,program_state,program_prepared,
  program_phase,program_loops,program_ignored,program_kind,program_source,program_edge,program_base,
  program_count,program_rise,program_target,program_fall,program_repeats,program_linear,program_gate);
 gate_engine #(.DAC_BITS(DAC_BITS),.ADDR_BITS(ADDR_BITS)) u_gate(clk,rst,
  gate_selected && (active_program ? program_arm:arm),disarm,abort_run,
  active_program ? program_rising:trigger_rise[active_trigger],active_program ? program_falling:trigger_fall[active_trigger],active_valid && gate_selected,
  active_initial,active_safe,play_target,
  play_count,play_rise,play_fall,
  gate_read_en,gate_read_index,sample_descriptor,gate_state,gate_prepared,gate_code,gate_index,gate_loops,gate_ignored,gate_phase);
 sample_engine #(.DAC_BITS(DAC_BITS),.ADDR_BITS(ADDR_BITS)) u_samples(clk,rst,
  fixed_selected && (active_program ? program_arm:arm),disarm,abort_run,
  active_program ? program_start:soft_trigger,selected_trigger,active_valid && fixed_selected,
  active_program ? 1'b0:active_external,active_initial,active_safe,
  play_repeats,play_count,fixed_read_en,fixed_read_index,
  sample_descriptor,fixed_state,fixed_prepared,fixed_code,fixed_index,fixed_loops,fixed_ignored);
 linear_engine #(.DAC_BITS(DAC_BITS),.ADDR_BITS(ADDR_BITS)) u_linear(clk,rst,
  linear_selected && (active_program ? program_arm:arm),disarm,abort_run,
  active_program ? program_start:soft_trigger,selected_trigger,
  active_program ? program_rising:trigger_rise[active_trigger],active_program ? program_falling:trigger_fall[active_trigger],
  active_valid && linear_selected,active_program ? 1'b0:active_external,use_gate,
  active_initial,active_safe,play_target,play_repeats,play_count,play_rise,play_fall,
  linear_read_en,linear_read_index,sample_descriptor,linear_state,linear_prepared,linear_code,linear_index,linear_loops,linear_ignored,linear_phase);
 scan_engine #(.DAC_BITS(DAC_BITS)) u_scan(clk,rst,arm && active_scan,disarm,abort_run,
  soft_trigger,selected_trigger,active_valid && active_scan,active_external,active_initial,active_safe,
  active_scan_start,active_scan_step,active_scan_points,active_scan_loop,
  scan_state,scan_prepared,scan_code,scan_position,scan_loops,scan_ignored);
 // Add the stable pool base in parallel, then select the engine address.
 // Keeping each sum avoids a mode mux followed by a carry chain at BRAM.
 (* keep="true" *) wire [RUN_ADDR_BITS-1:0] fixed_address=fixed_read_index[RUN_ADDR_BITS-1:0]+play_base;
 (* keep="true" *) wire [RUN_ADDR_BITS-1:0] gate_address=gate_read_index[RUN_ADDR_BITS-1:0]+play_base;
 (* keep="true" *) wire [RUN_ADDR_BITS-1:0] linear_address=linear_read_index[RUN_ADDR_BITS-1:0]+play_base;
 wire [RUN_ADDR_BITS-1:0] playback_address=use_linear ? linear_address:use_gate ? gate_address:fixed_address;
 wire [RUN_ADDR_BITS:0] sample_memory_address=command_state==10 ? {!active_bank,verify_index[RUN_ADDR_BITS-1:0]}:{active_bank,playback_address};
 always @(posedge clk) begin
  sample_descriptor<=sample_memory[sample_memory_address];
  if(sample_write_en)sample_memory[sample_write_address]<=sample_write_data;
 end
 always @(posedge clk) descriptor<=memory[{active_bank,read_index}];
 // Registered synchronous write control keeps asynchronous lock/reset signals
 // away from the BRAM write enable pins.
 always @(posedge clk) if(memory_write_en) memory[memory_write_address]<=memory_write_data;
 always @(posedge clk) begin
  if(rst) begin
   response<=0; response_valid<=0; previous_request<=0; cached_response<=0; cache_valid<=0; incoming<=0; equal_chunks<=0;
   command_state<=0; batch_left<=0;batch_payload<=0; error_code<=0; work<=0; cmd<=0; active_bank<=0; memory_write_en<=0; memory_write_address<=0; memory_write_data<=0;
   pending_valid<=0; active_valid<=0; loaded<=0; pending_count<=0; active_count<=0;
   pending_initial<=MID_CODE; active_initial<=MID_CODE; pending_safe<=MID_CODE; active_safe<=MID_CODE; previous_target<=0;
   pending_divider<=200; active_divider<=200; pending_repeats<=1; active_repeats<=1;
   pending_external<=1; active_external<=1; pending_id<=0; active_id<=0;
   arm<=0; disarm<=0; abort_run<=0; soft_trigger<=0; divisor<=0; division_rem<=0; quotient<=0; upload_target<=0;
   division_next<=0; division_can_subtract<=0;
   legacy_fm_code<=512; fm_initial<=512; fm_step<=1; fm_points<=1024; fm_counter<=0;
   pending_fm_initial<=0; pending_fm_step<=1; pending_fm_points<=1024;
   active_scan<=0;pending_scan<=0;active_scan_loop<=0;pending_scan_loop<=0;
   active_scan_start<=0;pending_scan_start<=0;active_scan_step<=0;pending_scan_step<=0;active_scan_points<=1;pending_scan_points<=1;
   active_program<=0;pending_program<=0;active_steps<=0;pending_steps<=0;steps_loaded<=0;step_write_en<=0;step_write_address<=0;step_write_data<=0;
   active_gate<=0; pending_gate<=0; active_rise<=0; pending_rise<=0; active_fall_ticks<=1; pending_fall_ticks<=1; active_target<=MID_CODE; pending_target<=MID_CODE;
   active_samples<=0; pending_samples<=0;pending_program<=0; active_channel<=0; pending_channel<=0;
   active_linear<=0;pending_linear<=0;verify_phase<=0;verify_step<=0;verify_index<=0;verify_end<=0;verify_rise_end<=0;verify_gate<=0;verify_target<=0;verify_fall_ticks<=0;verify_fall_sum<=0;
   active_trigger<=0; pending_trigger<=0; active_falling<=0; pending_falling<=0;
   sample_loaded<=0; pending_runs<=0; active_runs<=0;
   sample_write_en<=0; sample_write_address<=0; sample_write_data<=0;
   active_boot<=BOOT_CONFIG[0] && boot_available;
   if(BOOT_CONFIG[0] && boot_available) begin
    active_valid<=1; active_samples<=1; arm<=1;
    active_linear<=BOOT_CONFIG[7];
    active_program<=BOOT_CONFIG[4];active_steps<=BOOT_CONFIG[190:184];
    active_scan<=BOOT_CONFIG[5];active_scan_loop<=BOOT_CONFIG[6];active_scan_start<=BOOT_CONFIG[192+:DAC_BITS];
    active_scan_step<=BOOT_CONFIG[223:208];active_scan_points<=BOOT_CONFIG[239:224];
    active_gate<=BOOT_CONFIG[1]; active_external<=BOOT_CONFIG[2]; active_falling<=BOOT_CONFIG[3];
    active_initial<=BOOT_CONFIG[16+:DAC_BITS]; active_safe<=BOOT_CONFIG[32+:DAC_BITS];
    active_repeats<=BOOT_CONFIG[63:48]; active_id<=BOOT_CONFIG[95:64]; active_runs<=BOOT_CONFIG[96+:ADDR_BITS+1];
    active_rise<=BOOT_CONFIG[112+:ADDR_BITS+1]; active_fall_ticks<=BOOT_CONFIG[159:128];
    active_target<=BOOT_CONFIG[160+:DAC_BITS]; active_trigger<=BOOT_CONFIG[178:176];
   end
  end else begin
   response_valid<=0; arm<=0; disarm<=0; abort_run<=0; soft_trigger<=0; memory_write_en<=0; sample_write_en<=0;step_write_en<=0;
   if(active_valid && !active_samples && fm_trigger) begin
    if(fm_counter==0 || fm_counter>=fm_points) begin legacy_fm_code<=fm_initial; fm_counter<=1; end
    else begin legacy_fm_code<=fm_sum>1023 ? 10'd1023:fm_sum[9:0]; fm_counter<=fm_counter+1'b1; end
   end
   case(command_state)
    0: if(request_valid && response_ready) begin incoming<=request; command_state<=5; end
    5: begin
     for(chunk=0;chunk<8;chunk=chunk+1)
      equal_chunks[chunk]<=incoming[chunk*30+:30]==previous_request[chunk*30+:30];
     command_state<=6;
    end
    6: begin
     if(CACHE_ENABLED && cache_valid && (&equal_chunks)) begin response<=cached_response; response_valid<=1; command_state<=0; end
     else begin
      previous_request<=incoming[239:0];
      work<= {192'b0, incoming[47:32], (incoming[31:24]|8'h80),8'h01,16'h5aa5};
      error_code<=0; cmd<=incoming[31:24]; command_state<=7;
     end
    end
    7: begin
      command_state<=3;
      if(incoming[23:16]!=1) error_code<=1;
      else case(incoming[31:24])
       8'h01: begin
        work[87:56]<=CORE_HZ; work[103:88]<=16'd16; work[119:104]<=16'd64;
        work[127:120]<=DAC_BITS; work[135:128]<=8'd1; work[151:136]<=16'd1; work[167:152]<=RUN_CAPACITY; work[175:168]<=DAC_COUNT; work[183:176]<=TRIGGER_COUNT; work[191:184]<=8'd235 | (BOOT_CONFIG[0] ? 16:0);work[199:192]<=64;
        work[207:200]<=3 | (USB_FIFO_ENABLED ? 4:0) | (UART_AUTOBAUD ? 16:0);work[215:208]<=32;work[223:216]<=1;work[231:224]<=1;work[239:232]<=3;
       end
       8'h02: begin
        work[63:56]<={6'b0,run_state}; work[71:64]<=active_program ? {2'b0,program_step}:active_samples ? sample_index[7:0]:{2'b0,segment_index};
        work[87:72]<=selected_code; work[103:88]<=completed_loops;
        work[135:104]<=active_id; work[167:136]<=ignored;
        work[175:168]<={7'b0,active_valid}; work[183:176]<=8'd1; work[191:184]<={5'b0,active_channel}; work[207:192]<=sample_index; work[215:208]<={6'b0,(active_samples && use_linear),active_samples}; work[223:216]<={5'b0,active_program ? program_source:active_trigger}; work[231:224]<=active_program ? {program_prepared,3'b0,(program_phase>3 ? 3'b0:program_phase),program_edge}:{(use_linear && prepared),6'b0,active_falling}; work[239:232]<={use_gate,active_boot,active_program,(active_valid && active_samples && !use_gate && !active_program && active_repeats==0),1'b0,selected_gate_phase & {3{use_gate}}};
        if(active_scan) begin
         work[71:64]<=scan_position[7:0];work[207:192]<=scan_position;
         work[231:224]<={scan_prepared,6'b0,active_falling};work[239:232]<={1'b0,active_boot,1'b0,active_scan_loop,1'b1,3'b0};
        end
       end
       8'h10: begin
        if(!LEGACY_ENABLED)error_code<=1;
        else if(configuration_busy) error_code<=2;
        else if(DAC_BITS!=10 || payload[15:0]>1023 || payload[31:16]<16 ||
         payload[47:32]==0 || payload[47:32]>64 || payload[63:48]==0 ||
         payload[103:96]>1 || payload[127:112]>1023 ||
         payload[143:128]>1023 || payload[159:144]>1023 || payload[175:160]==0 || payload[175:160]>1024) error_code<=3;
        else begin
         pending_initial<=payload[9:0]; previous_target<=payload[9:0]; pending_divider<=payload[31:16];
         pending_count<=payload[38:32]; pending_repeats<=payload[63:48]; pending_id<=payload[95:64];
         pending_external<=payload[96]; pending_safe<=payload[121:112];
         pending_fm_initial<=payload[137:128]; pending_fm_step<=payload[153:144]; pending_fm_points<=payload[170:160];
         pending_linear<=0;pending_gate<=0; pending_valid<=1; loaded<=0; pending_samples<=0;pending_program<=0;pending_scan<=0; pending_channel<=0; pending_trigger<=0; pending_falling<=0;
        end
       end
       8'h11: begin
        if(!LEGACY_ENABLED) error_code<=1;
        else if(configuration_busy) error_code<=2;
        else if(!pending_valid || pending_samples) error_code<=4;
        else if(payload[15:0]!={9'b0,loaded} || loaded>=pending_count || payload[31:16]>1023 || payload[63:32]==0) error_code<=3;
        else begin
         divisor<=payload[63:32]; quotient<=0; upload_target<=payload[25:16];
         division_rem<=payload[25:16]>=previous_target ? payload[25:16]-previous_target:previous_target-payload[25:16];
         command_state<=8;
        end
       end
       8'h12: begin
        if(configuration_busy) error_code<=2;
        else if(!commit_ready) error_code<=4;
        else if(!commit_loop_valid) error_code<=3;
        else if(pending_linear) begin verify_step<=0;verify_phase<=0;command_state<=10;end
        else command_state<=11;
       end
       8'h30,8'h34: begin
        if(configuration_busy) error_code<=2;
        else if(!begin_sample_valid) error_code<=3;
        else begin
         pending_linear<=cmd==8'h34;pending_scan<=0;pending_program<=0;pending_gate<=0; pending_initial<=payload[0+:DAC_BITS]; pending_safe<=payload[16+:DAC_BITS]; pending_runs<=payload[32+:ADDR_BITS+1];
         pending_repeats<=payload[63:48]; pending_id<=payload[95:64]; pending_external<=payload[96];
         pending_channel<=payload[106:104];
         pending_trigger<=payload[128] ? payload[114:112]:payload[106:104];
         pending_falling<=payload[128] && payload[120]; pending_samples<=1; pending_valid<=1; sample_loaded<=0;
        end
       end
       8'h32,8'h35: begin
        if(configuration_busy) error_code<=2;
        else if(!begin_gate_valid) error_code<=3;
        else begin
         pending_linear<=cmd==8'h35;pending_scan<=0;pending_program<=0;pending_gate<=1; pending_samples<=1; pending_valid<=1; sample_loaded<=0;
         pending_initial<=payload[0+:DAC_BITS]; pending_safe<=payload[16+:DAC_BITS]; pending_runs<=payload[32+:ADDR_BITS+1];
         pending_rise<=payload[48+:ADDR_BITS+1]; pending_id<=payload[95:64]; pending_channel<=payload[98:96];
         pending_trigger<=payload[106:104]; pending_target<=payload[112+:DAC_BITS]; pending_fall_ticks<=payload[159:128];
         pending_repeats<=1; pending_external<=1; pending_falling<=0;
        end
       end
       8'h50,8'h52: begin
        if(configuration_busy)error_code<=2;
        else if(!program_fields_valid)error_code<=3;
        else begin
         pending_linear<=cmd==8'h52;pending_scan<=0;pending_program<=1;pending_gate<=0;pending_samples<=1;pending_valid<=1;sample_loaded<=0;steps_loaded<=0;
         pending_initial<=payload[0+:DAC_BITS];pending_safe<=payload[16+:DAC_BITS];pending_runs<=payload[32+:ADDR_BITS+1];
         pending_steps<=payload[54:48];pending_id<=payload[95:64];pending_channel<=payload[98:96];
         pending_repeats<=1;pending_external<=1;pending_trigger<=0;pending_falling<=0;
        end
       end
       8'h51: begin
        if(configuration_busy)error_code<=2;
        else if(!pending_valid || !pending_program)error_code<=4;
        else if(!step_fields_valid)error_code<=3;
        else begin
         step_write_en<=1;step_write_address<={!active_bank,steps_loaded[5:0]};step_write_data<=payload;steps_loaded<=steps_loaded+1'b1;
        end
       end
       8'h33: command_state<=9; // Allow the signed endpoint validation pipeline to settle.
       8'h36: begin
        if(configuration_busy)error_code<=2;
        else if(!pending_valid || !pending_samples)error_code<=4;
        else if(!batch_fields_valid)error_code<=3;
        else begin batch_payload<=payload;batch_left<=payload[17:16];command_state<=12;end
       end
       8'h31: begin
        if(configuration_busy) error_code<=2;
        else if(!pending_valid || !pending_samples) error_code<=4;
        else if(!run_fields_valid) error_code<=3;
        else begin
         sample_write_en<=1; sample_write_address<={!active_bank,sample_loaded[RUN_ADDR_BITS-1:0]};
         sample_write_data<={payload[16+:DAC_BITS],payload[63:32]}; sample_loaded<=sample_loaded+1'b1;
        end
       end
       8'h20: if(configuration_busy) error_code<=2;
        else if(!active_valid) error_code<=4;
        else begin arm<=1; command_state<=2; end
       8'h21: if(run_state!=1 || !prepared || active_external) error_code<=2; else soft_trigger<=1;
       8'h22: begin abort_run<=1; legacy_fm_code<=fm_initial; fm_counter<=0; end
       8'h23: if(run_state==2) error_code<=2; else disarm<=1;
       default: error_code<=1;
      endcase
    end
    12: begin
     sample_write_en<=1;sample_write_address<={!active_bank,sample_loaded[RUN_ADDR_BITS-1:0]};
     sample_write_data<={batch_payload[32+:DAC_BITS],batch_payload[48+:32]};
     // Fixed slices avoid a wide variable-index mux at the 200 MHz RAM port.
     batch_payload[191:32]<={48'b0,batch_payload[191:80]};
     sample_loaded<=sample_loaded+1'b1;batch_left<=batch_left-1'b1;
     command_state<=batch_left==1 ? 3:12;
    end
    9: begin
     command_state<=3;
     if(configuration_busy)error_code<=2;
     else if(!(&scan_checks))error_code<=3;
     else begin
      pending_linear<=0;pending_scan<=1;pending_program<=0;pending_gate<=0;pending_samples<=1;pending_valid<=1;sample_loaded<=0;pending_runs<=0;
      pending_initial<=payload[0+:DAC_BITS];pending_safe<=payload[16+:DAC_BITS];pending_scan_start<=payload[32+:DAC_BITS];
      pending_scan_step<=payload[63:48];pending_scan_points<=payload[79:64];pending_id<=payload[111:80];
      pending_channel<=payload[114:112];pending_trigger<=payload[122:120];pending_external<=payload[128];
      pending_falling<=payload[129];pending_scan_loop<=payload[130];pending_repeats<=1;
     end
    end
    10: begin
     case(verify_phase)
      0: begin
       verify_fall_sum<=0;
       if(pending_program)verify_phase<=1;
       else begin
        verify_index<=0;verify_end<=pending_runs;verify_rise_end<=pending_rise;
        verify_gate<=pending_gate;verify_target<=pending_target;verify_fall_ticks<=pending_fall_ticks;
        verify_phase<=3;
       end
      end
      1: verify_phase<=2; // synchronous step RAM latency
      2: begin
       if(!verify_step_descriptor[176]) begin
        if(verify_last_step)command_state<=11;
        else begin verify_step<=verify_step+1'b1;verify_phase<=0;end
       end else begin
        verify_index<=verify_step_descriptor[32+:ADDR_BITS+1];
        verify_end<=verify_step_descriptor[32+:ADDR_BITS+1]+verify_step_descriptor[48+:ADDR_BITS+1];
        verify_rise_end<=verify_step_descriptor[32+:ADDR_BITS+1]+verify_step_descriptor[64+:ADDR_BITS+1];
        verify_gate<=verify_step_descriptor[15:8]==1;verify_target<=verify_step_descriptor[80+:DAC_BITS];
        verify_fall_ticks<=verify_step_descriptor[127:96];verify_phase<=3;
       end
      end
      3: verify_phase<=4; // synchronous BRAM read
      4: verify_phase<=5; // descriptor capture isolates BRAM clock-to-output delay
      5: verify_phase<=6; // registered return-duration sum
      6: verify_phase<=7; // registered descriptor/range comparisons
      7: begin
       if(|verify_errors) begin
        error_code<=3;command_state<=3;
       end else begin
        if(verify_gate && verify_returning)verify_fall_sum<=verify_next_sum[31:0];
        if(verify_last) begin
         if(!pending_program || verify_last_step)command_state<=11;
         else begin verify_step<=verify_step+1'b1;verify_phase<=0;end
        end else begin verify_index<=verify_index+1'b1;verify_phase<=3;end
       end
      end
     endcase
    end
    11: begin
     active_linear<=pending_linear;active_program<=pending_program;active_steps<=pending_steps;
     active_scan<=pending_scan;active_scan_start<=pending_scan_start;active_scan_step<=pending_scan_step;
     active_scan_points<=pending_scan_points;active_scan_loop<=pending_scan_loop;
     active_gate<=pending_gate;active_rise<=pending_rise;active_fall_ticks<=pending_fall_ticks;active_target<=pending_target;
     active_bank<=!active_bank;active_valid<=1;active_boot<=0;pending_valid<=0;active_samples<=pending_samples;
     active_channel<=pending_channel;active_runs<=pending_runs;active_trigger<=pending_trigger;active_falling<=pending_falling;
     active_initial<=pending_initial;active_safe<=pending_safe;active_count<=pending_count;
     active_divider<=pending_divider;active_repeats<=pending_repeats;active_external<=pending_external;active_id<=pending_id;
     fm_initial<=pending_fm_initial;fm_step<=pending_fm_step;fm_points<=pending_fm_points;
     fm_counter<=0;legacy_fm_code<=pending_fm_initial;disarm<=1;command_state<=3;
    end
    // The numerator is a 10-bit DAC difference. Pipeline the comparison and
    // subtraction away from the command FSM's control fanout at 200 MHz.
    8: begin
        division_can_subtract<=(divisor[31:10]==0 && division_rem>=divisor[9:0]);
        division_next<=division_rem-divisor[9:0]; command_state<=1;
       end
    1: if(division_can_subtract) begin division_rem<=division_next; quotient<=quotient+1'b1; command_state<=8; end
       else begin
        memory_write_address<={!active_bank,loaded[5:0]}; memory_write_data<={upload_target,quotient,22'b0,division_rem,divisor}; memory_write_en<=1;
        loaded<=loaded+1'b1; previous_target<=upload_target; command_state<=3;
       end
    2: if(prepared && run_state==1) command_state<=3;
    3: begin work[55:48]<=error_code; command_state<=4; end
    4: if(response_ready) begin response<=work; cached_response<=work; cache_valid<=1;
        response_valid<=1; command_state<=0; end
   endcase
  end
 end
endmodule
