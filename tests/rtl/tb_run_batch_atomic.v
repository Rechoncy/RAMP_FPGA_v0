`timescale 1ns/1ps
module tb_run_batch_atomic;
 parameter DAC_BITS=14;
 reg clk=0,rst=1;always #2.5 clk=~clk;
 reg [255:0] request=0;reg request_valid=0;
 wire [239:0] response;wire response_valid;
 wire [DAC_BITS-1:0] code;wire [5:0] monitor;
 integer seq=0,writes=0,i;
 reg [191:0] begin_payload,valid_batch,bad_batch;
 reg [DAC_BITS+31:0] expected_descriptor;
 ramp_controller #(.DAC_COUNT(1),.DAC_BITS(DAC_BITS),.RUN_ADDR_BITS(3),.CACHE_ENABLED(0),.LEGACY_ENABLED(0)) dut(
  clk,rst,request,request_valid,response,response_valid,1'b1,2'b0,2'b0,code,monitor);
 always @(posedge clk)if(!rst && dut.sample_write_en)writes=writes+1;
 task command(input [7:0] cmd,input [191:0] payload,input [7:0] expected);
 begin
  @(negedge clk);seq=seq+1;
  request={16'b0,payload,seq[15:0],cmd,8'h01,16'h5aa5};request_valid=1;
  @(negedge clk);request_valid=0;
  wait(response_valid);@(negedge clk);
  if(response[55:48]!==expected)$fatal(1,"command %h response %d expected %d",cmd,response[55:48],expected);
 end endtask
 task reject_without_write(input [191:0] payload);
 integer before_writes;reg [13:0] before_loaded;
 begin
  before_writes=writes;before_loaded=dut.sample_loaded;
  command(8'h36,payload,3);
  if(writes!=before_writes || dut.sample_loaded!==before_loaded)
   $fatal(1,"invalid RUN_BATCH partially modified pending bank");
 end endtask
 initial begin
  repeat(5)@(negedge clk);rst=0;
  begin_payload=0;begin_payload[15:0]=100;begin_payload[31:16]=200;
  begin_payload[47:32]=3;begin_payload[63:48]=1;begin_payload[95:64]=32'habcdef01;
  valid_batch=0;valid_batch[23:16]=3;
  for(i=0;i<3;i=i+1)begin
   valid_batch[32+i*48+:16]=100+i*100;valid_batch[48+i*48+:32]=1;
  end
  command(8'h30,begin_payload,0);
  bad_batch=valid_batch;bad_batch[128+:16]=1<<DAC_BITS;reject_without_write(bad_batch);
  bad_batch=valid_batch;bad_batch[144+:32]=0;reject_without_write(bad_batch);
  bad_batch=valid_batch;bad_batch[31:24]=1;reject_without_write(bad_batch);
  bad_batch=valid_batch;bad_batch[176]=1;reject_without_write(bad_batch);
  bad_batch=valid_batch;bad_batch[15:0]=1;reject_without_write(bad_batch);
  bad_batch=valid_batch;bad_batch[23:16]=0;reject_without_write(bad_batch);
  bad_batch=valid_batch;bad_batch[23:16]=4;reject_without_write(bad_batch);
  bad_batch=valid_batch;bad_batch[23:16]=2;reject_without_write(bad_batch); // unused slot must be zero
  command(8'h12,0,4); // incomplete bank remains uncommitted
  command(8'h36,valid_batch,0);
  if(writes!=3 || dut.sample_loaded!=3)$fatal(1,"valid hold batch not written exactly once");
  for(i=0;i<3;i=i+1)begin
   expected_descriptor[32+:DAC_BITS]=100+i*100;expected_descriptor[31:0]=1;
   if(dut.sample_memory[8+i]!==expected_descriptor)$fatal(1,"hold batch slice/shift changed slot %0d",i);
  end
  command(8'h12,0,0);
  command(8'h34,begin_payload,0);
  for(i=0;i<3;i=i+1)valid_batch[48+i*48+:32]=32;
  bad_batch=valid_batch;bad_batch[144+:32]=31;reject_without_write(bad_batch);
  // Two valid records, then reject a two-record batch that overflows this pool.
  bad_batch=valid_batch;bad_batch[23:16]=2;bad_batch[175:128]=0;
  command(8'h36,bad_batch,0);
  bad_batch[15:0]=2;reject_without_write(bad_batch);
  bad_batch=0;bad_batch[15:0]=2;bad_batch[23:16]=1;
  bad_batch[47:32]=300;bad_batch[79:48]=32;
  command(8'h36,bad_batch,0);command(8'h12,0,0);
  if(writes!=6 || dut.active_runs!=3 || !dut.active_linear)$fatal(1,"linear batch commit failed");
  for(i=0;i<3;i=i+1)begin
   expected_descriptor[32+:DAC_BITS]=100+i*100;expected_descriptor[31:0]=32;
   if(dut.sample_memory[i]!==expected_descriptor)$fatal(1,"linear batch slice/shift changed slot %0d",i);
  end
  $display("PASS RUN_BATCH atomic validation: width, ticks, reserved bytes, indexes, counts and linear minimum");$finish;
 end
 initial begin #100000;$fatal(1,"RUN_BATCH validation timeout");end
endmodule
