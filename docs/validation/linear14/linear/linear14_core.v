module linear14_core(input clk,rst,uart_rx,input [1:0] trigger_in,output uart_tx,output reg [55:0] dac_data);
 wire core_clk;
 BUFG core_buf(.I(clk),.O(core_clk));
 wire [1:0] rise,fall;
 trigger_sync sync0(core_clk,rst,trigger_in[0],rise[0],fall[0]);
 trigger_sync sync1(core_clk,rst,trigger_in[1],rise[1],fall[1]);
 wire [255:0] request; wire [239:0] response;
 wire req_valid,rsp_valid,rsp_ready;
 packet_link link(core_clk,rst,uart_rx,uart_tx,request,req_valid,response,rsp_valid,rsp_ready);
 wire [55:0] codes;
 parallel_controller #(.DAC_COUNT(4),.TRIGGER_COUNT(2),.DAC_BITS(14)) controller(core_clk,rst,request,req_valid,response,rsp_valid,rsp_ready,rise,fall,codes);
 always @(posedge core_clk) if(rst)dac_data<={4{14'd8192}};else dac_data<=codes;
endmodule
