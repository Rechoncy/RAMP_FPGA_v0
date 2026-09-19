`timescale 1ns/1ps
module tb_autobaud_playback;
 reg clk=0;always #5 clk=~clk;
 reg rst=1,rx=1,rise=0;wire tx,request_valid,response_valid,response_ready;
 wire [255:0] request;wire [239:0] response;wire [9:0] code;
 reg [255:0] frames[0:1],received;integer i,j,seen=0,accepted=0,outfile;
 real bit_ns=1000000000.0/115200;
 localparam [255:0] BOOT=(256'd2<<96)|(256'd512<<32)|(256'd512<<16)|256'd5;
 packet_link #(.CLK_HZ(100000000)) link(clk,rst,rx,tx,request,request_valid,response,response_valid,response_ready);
 ramp_controller #(.CORE_HZ(100000000),.DAC_COUNT(1),.TRIGGER_COUNT(1),.RUN_ADDR_BITS(11),
  .LEGACY_ENABLED(0),.UART_AUTOBAUD(1),.BOOT_CONFIG(BOOT),.BOOT_MEMORY_FILE("wave.mem"))
  control(clk,rst,request,request_valid,response,response_valid,response_ready,rise,1'b0,code,);
 always @(negedge clk)begin
  if(control.sample_state==2)begin
   if(code!==(seen%64<32 ? 10'd10:10'd900))$fatal(1,"waveform lost/repeated tick during UART training at %d",seen);
   seen=seen+1;
  end else if(seen!=0)$fatal(1,"training reset/stopped active playback");
  if(request_valid)begin
   if(request!==frames[accepted])$fatal(1,"training leaked command");accepted=accepted+1;
  end
 end
 task byte_out;input [7:0] value;integer b;
 begin rx=0;#(bit_ns);for(b=0;b<8;b=b+1)begin rx=value[b];#(bit_ns);end rx=1;#(bit_ns);end endtask
 task exchange;input integer n;integer b,k;
 begin
  fork
   begin for(b=0;b<32;b=b+1)byte_out(frames[n][b*8+:8]);end
   begin
    received=0;
    for(k=0;k<32;k=k+1)begin
     @(negedge tx);#(bit_ns*1.5);
     for(j=0;j<8;j=j+1)begin received[k*8+j]=tx;#(bit_ns);end
     if(!tx)$fatal(1,"stop bit");
    end
   end
  join
  $fdisplay(outfile,"%064h",received);#(bit_ns*4);
 end endtask
 initial begin
  $readmemh("info.hex",frames);outfile=$fopen("responses.hex","w");
  repeat(8)@(negedge clk);rst=0;
  wait(control.prepared);@(negedge clk);rise=1;@(negedge clk);rise=0;
  exchange(0);
  byte_out(8'ha5); // discard a pre-existing partial frame with BREAK
  rx=0;#10020000;rx=1;#10000;bit_ns=1000;
  for(i=0;i<16;i=i+1)byte_out(8'h55);
  #30000;exchange(1);
  if(accepted!=2 || seen<1000000)$fatal(1,"incomplete integration exercise");
  $fclose(outfile);$display("PASS real controller playback continuous across default -> 1M training (%d ticks)",seen);$finish;
 end
 initial begin #25000000;$fatal(1,"timeout");end
endmodule
