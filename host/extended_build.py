"""Generate optional FT232H + UART firmware from a portable board configuration.

Waveforms retain the existing per-channel BRAM pool. No external-memory IP or
board-specific DDR project is required by the generated platform.
"""
from pathlib import Path
from .hardware_interfaces import UsbFifoSettings


USB_DECL = '''input usb_clk, inout [7:0] usb_data,
 input usb_rxf_n,usb_txe_n,output usb_rd_n,usb_wr_n,usb_oe_n,usb_siwu_n'''
USB_NAMES = ('usb_clk','usb_data','usb_rxf_n','usb_txe_n','usb_rd_n','usb_wr_n','usb_oe_n','usb_siwu_n')


def generate_top(board):
    usb=UsbFifoSettings.from_dict(board.usb_fifo).enabled
    addr_bits=13 if board.dac_count==1 else 12 if board.dac_count==2 else 11 if board.dac_count<=4 else 10
    base_ports=f'''input sys_clk,input [{board.trigger_count-1}:0] trigger_in,input uart_rx,
 output uart_tx,gnd0,gnd1,output reg [{board.dac_count*board.dac_bits-1}:0] dac_data,
 output [{board.dac_count-1}:0] dac_clk'''
    ports=base_ports+(',\n '+USB_DECL if usb else '')
    module='ramp_extended_core' if board.clock_input=='differential' else 'ramp_extended_top'
    text=f'''`timescale 1ns/1ps
module {module} #(parameter [2047:0] BOOT_CONFIGS=0, parameter [16383:0] BOOT_FILES=0)({ports});
 wire clk_core,clk_dac,rst_core,rst_dac;
 clock_reset_adapter u_clock(sys_clk,clk_core,clk_dac,rst_core,rst_dac);
 wire [{board.trigger_count-1}:0] trigger_rise,trigger_fall;
 genvar i;
 generate for(i=0;i<{board.trigger_count};i=i+1) begin: inputs
  trigger_sync u_sync(clk_core,rst_core,trigger_in[i],trigger_rise[i],trigger_fall[i]);
 end endgenerate
 wire [255:0] request;wire [239:0] response;
 wire request_valid,response_valid,response_ready;
'''
    if usb:
        text+=f''' (* ASYNC_REG="TRUE" *) reg [2:0] usb_reset=3'b111;
 always @(posedge usb_clk or posedge rst_core)
  if(rst_core)usb_reset<=3'b111;else usb_reset<={{usb_reset[1:0],1'b0}};
 wire [255:0] uart_request,usb_request;
 wire [239:0] uart_response,usb_response;
 wire uart_request_valid,uart_response_valid,uart_response_ready;
 wire usb_request_valid,usb_request_ready,usb_response_valid,usb_response_ready;
 packet_link #(.BAUD({board.uart_baud}),.CLK_HZ({int(board.core_mhz*1e6)})) u_uart(clk_core,rst_core,uart_rx,uart_tx,
  uart_request,uart_request_valid,uart_response,uart_response_valid,uart_response_ready);
 usb_packet_link u_usb(.core_clk(clk_core),.core_rst(rst_core),.usb_clk(usb_clk),.usb_rst(usb_reset[2]),
  .usb_data(usb_data),.usb_rxf_n(usb_rxf_n),.usb_txe_n(usb_txe_n),
  .usb_rd_n(usb_rd_n),.usb_wr_n(usb_wr_n),.usb_oe_n(usb_oe_n),.usb_siwu_n(usb_siwu_n),
  .request(usb_request),.request_valid(usb_request_valid),.request_ready(usb_request_ready),
  .response(usb_response),.response_valid(usb_response_valid),.response_ready(usb_response_ready));
 dual_packet_router u_router(.clk(clk_core),.rst(rst_core),
  .uart_request(uart_request),.uart_request_valid(uart_request_valid),
  .uart_response(uart_response),.uart_response_valid(uart_response_valid),.uart_response_ready(uart_response_ready),
  .usb_request(usb_request),.usb_request_valid(usb_request_valid),.usb_request_ready(usb_request_ready),
  .usb_response(usb_response),.usb_response_valid(usb_response_valid),.usb_response_ready(usb_response_ready),
  .request(request),.request_valid(request_valid),.response(response),
  .response_valid(response_valid),.response_ready(response_ready));
'''
    else:
        text+=f''' packet_link #(.BAUD({board.uart_baud}),.CLK_HZ({int(board.core_mhz*1e6)})) u_link(clk_core,rst_core,uart_rx,uart_tx,
  request,request_valid,response,response_valid,response_ready);
'''
    text+=f''' wire [{board.dac_count*board.dac_bits-1}:0] dac_codes;
 parallel_controller #(.UART_AUTOBAUD(1),.CORE_HZ({int(board.core_mhz*1e6)}),.DAC_COUNT({board.dac_count}),.TRIGGER_COUNT({board.trigger_count}),.DAC_BITS({board.dac_bits}),
  .RUN_ADDR_BITS({addr_bits}),.USB_FIFO_ENABLED({int(usb)}),.SOURCE_TAGGED({int(usb)}),
  .BOOT_CONFIGS(BOOT_CONFIGS),.BOOT_FILES(BOOT_FILES)) u_controller(
  .clk(clk_core),.rst(rst_core),.request(request),.request_valid(request_valid),
  .response(response),.response_valid(response_valid),.response_ready(response_ready),
  .trigger_rise(trigger_rise),.trigger_fall(trigger_fall),.dac_codes(dac_codes));
 generate for(i=0;i<{board.dac_count};i=i+1) begin: outputs
  always @(posedge clk_dac)
   if(rst_dac)dac_data[i*{board.dac_bits}+:{board.dac_bits}]<={1<<(board.dac_bits-1)};
   else dac_data[i*{board.dac_bits}+:{board.dac_bits}]<=dac_codes[i*{board.dac_bits}+:{board.dac_bits}];
  ODDR #(.DDR_CLK_EDGE("SAME_EDGE")) u_clock(.C(clk_dac),.CE(1'b1),.D1(1'b1),.D2(1'b0),.R(1'b0),.S(1'b0),.Q(dac_clk[i]));
 end endgenerate
 assign gnd0=0;assign gnd1=0;
endmodule
'''
    if board.clock_input=='differential':
        outer=ports.replace('input sys_clk,','input sys_clk_p,sys_clk_n,',1).replace('output reg','output')
        names=['trigger_in','uart_rx','uart_tx','gnd0','gnd1','dac_data','dac_clk',*(USB_NAMES if usb else [])]
        text+='module ramp_extended_top #(parameter [2047:0] BOOT_CONFIGS=0, parameter [16383:0] BOOT_FILES=0)('+outer+');\n wire ref_clk;\n IBUFDS u_reference_buffer(.I(sys_clk_p),.IB(sys_clk_n),.O(ref_clk));\n'
        text+=' ramp_extended_core #(.BOOT_CONFIGS(BOOT_CONFIGS),.BOOT_FILES(BOOT_FILES)) core(.sys_clk(ref_clk),'+','.join(f'.{n}({n})' for n in names)+');\nendmodule\n'
    return text


def stage_platform(board,output):
    usb=UsbFifoSettings.from_dict(board.usb_fifo)
    if not usb.enabled:return None
    folder=Path(output).resolve().with_name(Path(output).name+'_interfaces')
    # Validate before writing source artifacts; never reuse a previous stage.
    top=generate_top(board)
    folder.mkdir(parents=True,exist_ok=False)
    (folder/'ramp_extended_top.v').write_text(top,encoding='utf-8')
    return folder
