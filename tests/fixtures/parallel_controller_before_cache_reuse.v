`timescale 1ns/1ps
// Independent engines with explicit channel selection and atomic group control.
// 0x40 SELECT(channel); 0x41 GROUP(command, channel mask).
module frozen_parallel_controller #(parameter CORE_HZ=200000000,DAC_COUNT=4,TRIGGER_COUNT=2,DAC_BITS=10,
 RUN_ADDR_BITS=(DAC_COUNT<=1 ? 13:DAC_COUNT<=2 ? 12:DAC_COUNT<=4 ? 11:10),
 parameter [2047:0] BOOT_CONFIGS=0, parameter [16383:0] BOOT_FILES=0)(
 input clk,rst,input [255:0] request,input request_valid,
 output reg [239:0] response,output reg response_valid,input response_ready,
 input [TRIGGER_COUNT-1:0] trigger_rise,trigger_fall,
 output [DAC_COUNT*DAC_BITS-1:0] dac_codes);
 reg [2:0] selected;
 reg [3:0] state;
 reg [255:0] incoming,child_request;
 reg [239:0] previous_request,cached_response,reply;
 reg cache_valid;
 reg [7:0] equal_chunks;
 reg [DAC_COUNT-1:0] destinations,received,dispatch;
 wire [DAC_COUNT-1:0] child_valid;
 wire [239:0] child_response [0:DAC_COUNT-1];
 wire [5:0] monitor [0:DAC_COUNT-1];
 reg [7:0] error_code,group_error,checked_group_error;
 reg [DAC_COUNT-1:0] arm_blocked,trigger_blocked,missing_config;
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
   .RUN_ADDR_BITS(RUN_ADDR_BITS),.CACHE_ENABLED(0),.LEGACY_ENABLED(0),.BOOT_CONFIG(BOOT_CONFIGS[c*256+:256]),
   .BOOT_MEMORY_FILE(BOOT_FILES[c*2048+:2048])) engine(
   clk,rst,child_request,dispatch[c],child_response[c],child_valid[c],1'b1,
   trigger_rise,trigger_fall,dac_codes[c*DAC_BITS+:DAC_BITS],monitor[c]);
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
   previous_request<=0;cached_response<=0;cache_valid<=0;equal_chunks<=0;error_code<=0;complete_mask<=0;
  end else begin
   response_valid<=0;dispatch<=0;
   case(state)
    0: if(request_valid && response_ready) begin incoming<=request;state<=1;end
    1: begin
     for(j=0;j<8;j=j+1) equal_chunks[j]<=incoming[j*30+:30]==previous_request[j*30+:30];
     state<=2;
    end
    2: if(cache_valid && (&equal_chunks)) begin response<=cached_response;state<=7;end
       else begin
        previous_request<=incoming[239:0];
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
       reply[207:200]<=1;reply[215:208]<=32;reply[223:216]<=1;
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
      if(incoming[31:24]!=8'h41) begin
       reply<=child_response[j];
       if(incoming[31:24]==8'h02) reply[191:184]<=selected;
      end
     end
     if((received & destinations)==destinations) state<=6;
    end
    6: begin reply[55:48]<=error_code;state<=8;end
    8: begin response<=reply;cached_response<=reply;cache_valid<=1;state<=7;end
    7: if(response_ready) begin response_valid<=1;state<=0;end
   endcase
  end
 end
endmodule
