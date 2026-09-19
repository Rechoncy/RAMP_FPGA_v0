`timescale 1ns/1ps
// Small real CDC FIFO makes full/ready withdrawal frequent. Reset while both
// reserved RX slots are occupied, then check ordered traffic in both directions.
module tb_ft245_registered;
 parameter real FLAG_DELAY=0;
 localparam RX_TOTAL=2048,TX_TOTAL=257;
 reg usb_clk=0,core_clk=0,rst=0;always #8.333 usb_clk=~usb_clk;always #2.5 core_clk=~core_clk;
 reg phase=0,rxf_n=1,txe_n=1;tri [7:0] data;reg [7:0] input_data=0;
 wire oe_n,rd_n,wr_n,siwu_n;wire [7:0] rx_data,fifo_data;
 wire rx_valid,rx_ready,fifo_valid;reg fifo_ready=0;
 reg [7:0] tx_data=0;reg tx_valid=0;wire tx_ready;
 integer usb_sent=0,consumed=0,tx_sent=0,external_received=0,usb_cycle=0,core_cycle=0;
 integer full_cycles=0,skid_full_cycles=0,burst=0,max_burst=0,oe_cycles=0;
 integer full_pop_cycles=0,replace_cycles=0,rxf_blocked_cycles=0;
 reg last_tx_accept=0;reg [15:0] usb_random=16'hace1,core_random=16'h6789;
 reg before_rd,before_wr,before_oe;
 assign data=!oe_n && !rxf_n ? input_data:8'bz;
 ft245_sync dut(usb_clk,rst,data,rxf_n,txe_n,oe_n,rd_n,wr_n,siwu_n,
                 rx_data,rx_valid,rx_ready,tx_data,tx_valid,tx_ready);
 usb_async_fifo #(.ADDR_BITS(3)) fifo(usb_clk,rst,rx_data,rx_valid,rx_ready,
                                    core_clk,rst,fifo_data,fifo_valid,fifo_ready);
 function [7:0] value(input integer index,input integer salt);
 begin value=(index*37+(index>>3)*11+salt*79)&255;end
 endfunction
 always @(negedge core_clk)begin
  if(rst)begin fifo_ready=0;core_cycle=0;core_random=16'h6789;end
  else begin
   core_cycle=core_cycle+1;
   core_random={core_random[14:0],core_random[15]^core_random[13]^core_random[12]^core_random[10]};
   fifo_ready=phase && core_cycle%271>190 && core_random[0];
  end
 end
 always @(posedge core_clk)begin
  if(rst)consumed=0;
  else if(fifo_valid && fifo_ready)begin
   if(consumed>=RX_TOTAL || fifo_data!==value(consumed,phase))
    $fatal(1,"registered RX lost/duplicated byte %0d: got %h",consumed,fifo_data);
   consumed=consumed+1;
  end
 end
 always @(negedge usb_clk)begin
  if(rst)begin tx_valid=0;tx_data=0;end
  else if(!tx_valid || last_tx_accept)begin
   tx_valid=phase && tx_sent<TX_TOTAL && usb_cycle%91<10;
   tx_data=value(tx_sent,7);
  end
 end
 always @(posedge usb_clk)begin
  if(rst)begin
   usb_sent=0;tx_sent=0;external_received=0;usb_cycle=0;full_cycles=0;skid_full_cycles=0;
   full_pop_cycles=0;replace_cycles=0;rxf_blocked_cycles=0;
   burst=0;max_burst=0;oe_cycles=0;last_tx_accept=0;rxf_n=1;txe_n=1;usb_random=16'hace1;
  end else begin
   usb_cycle=usb_cycle+1;
   if(!oe_n)oe_cycles=oe_cycles+1;else oe_cycles=0;
   if(!rd_n && (oe_n || !wr_n || oe_cycles<2))$fatal(1,"OE setup or read direction violation");
   if(!wr_n && (!oe_n || !rd_n))$fatal(1,"write direction violation");
   if(dut.receive_count>2)$fatal(1,"two-slot RX reservation overflow");
   // A full two-slot queue never has an outstanding RD# reservation. After a
   // pop it reopens RD# for the following cycle; full + pop + read is therefore
   // unreachable. A one-slot queue can replace its head every USB clock.
   if(dut.receive_count==2 && !rd_n)$fatal(1,"full skid still reserved an external byte");
   if(dut.receive_count==2 && rx_ready)full_pop_cycles=full_pop_cycles+1;
   if(dut.receive_count==1 && rx_ready && !rd_n && !rxf_n)replace_cycles=replace_cycles+1;
   if(!rd_n && rxf_n)begin
    rxf_blocked_cycles=rxf_blocked_cycles+1;
    if(dut.read_accept || dut.receive_after!={1'b0,dut.receive_count}-{2'b0,dut.receive_pop})
     $fatal(1,"RXF# high was incorrectly accepted");
   end
   if(!rx_ready)full_cycles=full_cycles+1;
   if(dut.receive_count==2)skid_full_cycles=skid_full_cycles+1;
   if(!rd_n && !rxf_n)begin usb_sent=usb_sent+1;burst=burst+1;end else burst=0;
   if(burst>max_burst)max_burst=burst;
   last_tx_accept=tx_valid && tx_ready;
   if(last_tx_accept)tx_sent=tx_sent+1;
   if(!wr_n && !txe_n)begin
    if(external_received>=TX_TOTAL || data!==value(external_received,7))
     $fatal(1,"registered TX lost/duplicated byte %0d",external_received);
    external_received=external_received+1;
   end
   #FLAG_DELAY;
   before_rd=rd_n;before_wr=wr_n;before_oe=oe_n;
   usb_random={usb_random[14:0],usb_random[15]^usb_random[13]^usb_random[12]^usb_random[10]};
   rxf_n=usb_sent>=RX_TOTAL || (phase && usb_random[3:0]==0);
   txe_n=usb_random[4] && usb_random[6];
   input_data=value(usb_sent,phase);
   #0.001;
   if(FLAG_DELAY>0 && (rd_n!==before_rd || wr_n!==before_wr || oe_n!==before_oe))
    $fatal(1,"external flags changed a registered output between clocks");
  end
 end
 initial begin
  #1;rst=1;#70;rst=0;
  wait(!rx_ready && dut.receive_count==2);repeat(5)@(negedge usb_clk);
  if(usb_sent!=10 || !rd_n)$fatal(1,"FIFO8 + skid2 reservation boundary read %0d bytes",usb_sent);
  rst=1;#41;phase=1;rst=0;
  wait(consumed==RX_TOTAL && external_received==TX_TOTAL);repeat(20)@(negedge usb_clk);
  if(usb_sent!=RX_TOTAL || tx_sent!=TX_TOTAL || fifo_valid || dut.receive_count!=0 ||
     full_cycles==0 || skid_full_cycles==0 || full_pop_cycles==0 || replace_cycles==0 ||
     rxf_blocked_cycles==0 || max_burst<8)
   $fatal(1,"coverage/full/continuous-read failure: sent=%0d tx=%0d full=%0d skid=%0d burst=%0d",usb_sent,tx_sent,full_cycles,skid_full_cycles,max_burst);
  $display("PASS registered FT245: full reservation, reset, continuous RX, random ready and both directions");$finish;
 end
 initial begin #2000000;$fatal(1,"registered FT245 timeout rx=%0d tx=%0d",consumed,external_received);end
endmodule
