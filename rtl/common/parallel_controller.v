`timescale 1ns/1ps
// Independent engines with explicit channel selection and atomic group control.
// 0x40 SELECT(channel); 0x41 GROUP(command, channel mask).
module parallel_controller #(parameter CORE_HZ=200000000,DAC_COUNT=4,TRIGGER_COUNT=2,DAC_BITS=10,
 USB_FIFO_ENABLED=0, SOURCE_TAGGED=0,
 RUN_ADDR_BITS=(DAC_COUNT<=1 ? 13:DAC_COUNT<=2 ? 12:DAC_COUNT<=4 ? 11:10),
 parameter [2047:0] BOOT_CONFIGS=0, parameter [16383:0] BOOT_FILES=0, parameter UART_AUTOBAUD=0)(
 input clk,rst,input [255:0] request,input request_valid,
 output reg [239:0] response,output reg response_valid,input response_ready,
 input [TRIGGER_COUNT-1:0] trigger_rise,trigger_fall,
 output [DAC_COUNT*DAC_BITS-1:0] dac_codes);
 reg [2:0] selected;
 reg [3:0] state;
 reg [255:0] incoming,child_request;
 reg [239:0] previous_request,reply;
 // response already retains the last completed reply until the next state 8.
 // Reuse it for retransmission, avoiding a duplicate 240-bit enabled bank.
 // Both old registers were reset/written together; a cache hit changes no data.
 wire [239:0] cached_response=response;
 reg cache_valid;
 reg previous_source;
 reg [7:0] equal_chunks;
 reg [DAC_COUNT-1:0] destinations,received,dispatch;
 wire [DAC_COUNT-1:0] child_valid;
 wire [239:0] child_response [0:DAC_COUNT-1];
 wire [5:0] monitor [0:DAC_COUNT-1];
 reg [7:0] error_code,group_error,checked_group_error;
 reg [DAC_COUNT-1:0] arm_blocked,trigger_blocked,missing_config;
 wire [TRIGGER_COUNT-1:0] debug_mask,debug_levels,debug_rise,debug_fall;
 wire debug_running,debug_expired;
 wire [2:0] debug_input;
 wire [31:0] debug_emitted;
 reg debug_configure,debug_action,debug_heartbeat,debug_release,debug_owner;
 reg internal_abort,debug_stopping,resume_request;
 wire [TRIGGER_COUNT-1:0] routed_rise=(trigger_rise & ~debug_mask) | debug_rise;
 wire [TRIGGER_COUNT-1:0] routed_fall=(trigger_fall & ~debug_mask) | debug_fall;
 trigger_debug #(.COUNT(TRIGGER_COUNT),.CORE_HZ(CORE_HZ)) debug_bank(
  .clk(clk),.rst(rst),.configure(debug_configure),.action(debug_action),
  .heartbeat(debug_heartbeat),.release_debug(debug_release),.halt(debug_stopping),
  .requested_mask(incoming[48+:TRIGGER_COUNT]),.input_index(incoming[50:48]),
  .operation(incoming[57:56]),.high_ticks(incoming[95:64]),
  .period_ticks(incoming[127:96]),.pulse_count(incoming[159:128]),
  .mask(debug_mask),.levels(debug_levels),.rising(debug_rise),.falling(debug_fall),
  .running(debug_running),.expired(debug_expired),.active_input(debug_input),.emitted(debug_emitted));
 integer g;
 always @(posedge clk) begin
  for(g=0;g<DAC_COUNT;g=g+1) begin
   arm_blocked[g]<=monitor[g][1:0]==1 || monitor[g][1:0]==2;
   missing_config[g]<=!monitor[g][3];
   trigger_blocked[g]<=monitor[g][1:0]!=1 || !monitor[g][2] || monitor[g][4];
  end
  checked_group_error<=group_error;
 end
 reg [DAC_COUNT-1:0] complete_mask;
 integer j,k;
 genvar c;
 generate for(c=0;c<DAC_COUNT;c=c+1) begin: channels
  ramp_controller #(.CORE_HZ(CORE_HZ),.DAC_COUNT(1),.TRIGGER_COUNT(TRIGGER_COUNT),.DAC_BITS(DAC_BITS),
   .RUN_ADDR_BITS(RUN_ADDR_BITS),.USB_FIFO_ENABLED(USB_FIFO_ENABLED),.CACHE_ENABLED(0),.LEGACY_ENABLED(0),.BOOT_CONFIG(BOOT_CONFIGS[c*256+:256]),
   .BOOT_MEMORY_FILE(BOOT_FILES[c*2048+:2048])) engine(
   clk,rst,child_request,dispatch[c],child_response[c],child_valid[c],1'b1,
   routed_rise,routed_fall,dac_codes[c*DAC_BITS+:DAC_BITS],monitor[c]);
 end endgenerate
 // Preflight rejects the whole group before issuing any command.
 always @* begin
  group_error=0;
  for(k=0;k<DAC_COUNT;k=k+1) if(incoming[56+k]) begin
   case(incoming[55:48])
    8'h20: if(arm_blocked[k]) group_error=2;
           else if(missing_config[k]) group_error=4;
    8'h21: if(trigger_blocked[k]) group_error=2;
    8'h22: ;
    default: group_error=1;
   endcase
  end
 end
 always @(posedge clk) begin
  if(rst) begin
   state<=0; selected<=0; incoming<=0; child_request<=0; response<=0; reply<=0;
   response_valid<=0; dispatch<=0; destinations<=0; received<=0;
   previous_request<=0;previous_source<=0;cache_valid<=0;equal_chunks<=0;error_code<=0;complete_mask<=0;
   debug_configure<=0;debug_action<=0;debug_heartbeat<=0;debug_release<=0;debug_owner<=0;
   internal_abort<=0;debug_stopping<=0;resume_request<=0;
  end else begin
   response_valid<=0;dispatch<=0;
   debug_configure<=0;debug_action<=0;debug_heartbeat<=0;debug_release<=0;
   case(state)
    0: if(debug_expired && debug_mask!=0) begin
        child_request<=0;child_request[23:16]<=1;child_request[31:24]<=8'h22;
        destinations<={DAC_COUNT{1'b1}};received<=0;internal_abort<=1;debug_stopping<=1;state<=4;
        // The transport has already handed off this pulse. Preserve it across
        // the autonomous abort, or a coincident request would deadlock its router.
        resume_request<=request_valid && response_ready;
        if(request_valid && response_ready) incoming<=request;
       end else if(request_valid && response_ready) begin incoming<=request;state<=1;end
    1: begin
     for(j=0;j<8;j=j+1) equal_chunks[j]<=incoming[j*30+:30]==previous_request[j*30+:30];
     state<=2;
    end
    2: if(cache_valid && (&equal_chunks) && (!SOURCE_TAGGED || incoming[240]==previous_source)) begin state<=7;end
       else begin
        previous_request<=incoming[239:0];previous_source<=incoming[240];
        reply<={192'b0,incoming[47:32],(incoming[31:24]|8'h80),8'h01,16'h5aa5};
        child_request<=incoming; error_code<=0;received<=0;state<=3;
       end
    3: begin
     state<=6;
     if(incoming[23:16]!=1) error_code<=1;
     else case(incoming[31:24])
      8'h01: begin
       reply[87:56]<=CORE_HZ;reply[103:88]<=16;reply[119:104]<=64;
       reply[127:120]<=DAC_BITS;reply[135:128]<=1;reply[151:136]<=2;
       reply[167:152]<=(1 << RUN_ADDR_BITS);reply[175:168]<=DAC_COUNT;
       reply[183:176]<=TRIGGER_COUNT;reply[191:184]<=239 | ((BOOT_CONFIGS!=0) ? 16:0);reply[199:192]<=64;
       reply[207:200]<=35 | (USB_FIFO_ENABLED ? 4:0) | (UART_AUTOBAUD ? 16:0);reply[215:208]<=32;reply[223:216]<=1;reply[231:224]<=1;reply[239:232]<=3;
      end
      8'h60: begin
       if((incoming[55:48] >> TRIGGER_COUNT)!=0) error_code<=3;
       else if((|arm_blocked) || debug_running || (debug_mask!=0 && SOURCE_TAGGED && debug_owner!=incoming[240])) error_code<=2;
       else begin debug_configure<=1;debug_owner<=incoming[240];end
      end
      8'h61: begin
       if(incoming[55:48]>=TRIGGER_COUNT || incoming[63:56]>3) error_code<=3;
       else if(debug_expired || !debug_mask[incoming[50:48]] || debug_running || (SOURCE_TAGGED && debug_owner!=incoming[240])) error_code<=2;
       else if(incoming[63:56]>=2 && (debug_levels[incoming[50:48]] || incoming[95:64]==0 || (incoming[63:56]==3 && incoming[127:96]<=incoming[95:64]))) error_code<=3;
       else debug_action<=1;
      end
      8'h62: begin
       if(incoming[48] && (!SOURCE_TAGGED || debug_owner==incoming[240])) debug_heartbeat<=1;
       reply[63:56]<=debug_mask;reply[71:64]<=debug_levels;
       reply[79:72]<={6'b0,debug_expired,debug_running};reply[87:80]<=debug_input;
       reply[119:88]<=debug_emitted;reply[127:120]<=1;
      end
      8'h63: begin
       // Explicit stop is always allowed, including recovery from another link.
       destinations<={DAC_COUNT{1'b1}};child_request[31:24]<=8'h22;
       child_request[239:48]<=0;debug_stopping<=1;state<=4;
      end
      8'h40: if(incoming[55:48]>=DAC_COUNT) error_code<=3;else selected<=incoming[50:48];
      8'h41: begin
       if(incoming[63:56]==0 || (incoming[63:56] >> DAC_COUNT)!=0) error_code<=3;
       else if(checked_group_error!=0) error_code<=checked_group_error;
       else begin
        destinations<=incoming[56+:DAC_COUNT];child_request[31:24]<=incoming[55:48];
        child_request[239:48]<=0;state<=4;
       end
      end
      // Legacy dual-output FM mode cannot be interpreted as an independent channel.
      8'h10,8'h11: error_code<=1;
      default: begin
       destinations<=1'b1 << selected;state<=4;
       if(incoming[31:24]==8'h30 || incoming[31:24]==8'h34) begin
        if(incoming[159:152]!=selected) begin error_code<=3;state<=6;end
        child_request[159:152]<=0;
        // Preserve legacy implicit trigger route before clearing local channel.
        if(!incoming[176]) begin child_request[176]<=1;child_request[167:160]<=incoming[159:152];child_request[175:168]<=0;end
       end
       if(incoming[31:24]==8'h32 || incoming[31:24]==8'h35 || incoming[31:24]==8'h50 || incoming[31:24]==8'h52) begin
        if(incoming[151:144]!=selected) begin error_code<=3;state<=6;end
        child_request[151:144]<=0;
       end
       if(incoming[31:24]==8'h33) begin
        if(incoming[167:160]!=selected) begin error_code<=3;state<=6;end
        child_request[167:160]<=0;
       end
      end
     endcase
    end
    4: begin dispatch<=destinations;state<=5;end
    5: begin
     received<=received | (child_valid & destinations);
     for(j=0;j<DAC_COUNT;j=j+1) if(child_valid[j] && destinations[j]) begin
      if(child_response[j][55:48]!=0) error_code<=child_response[j][55:48];
      if(incoming[31:24]!=8'h41 && !debug_stopping) begin
       reply<=child_response[j];
       if(incoming[31:24]==8'h02) reply[191:184]<=selected;
      end
     end
     if((received & destinations)==destinations) begin
      if(debug_stopping) begin
       debug_release<=1;debug_stopping<=0;
       if(internal_abort) begin internal_abort<=0;cache_valid<=0;state<=9;end
       else state<=6;
      end else state<=6;
     end
    end
    9: begin state<=resume_request ? 1:0;resume_request<=0;end
    6: begin reply[55:48]<=error_code;state<=8;end
    8: begin response<=reply;cache_valid<=1;state<=7;end
    7: if(response_ready) begin response_valid<=1;state<=0;end
   endcase
  end
 end
endmodule
