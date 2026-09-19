# Four-channel, 14-bit complete digital core comparison. No board programming.
# argv: RTL common directory; output directory; optional grouping stage.
# before_place (default) groups internal timing for a fresh implementation.
# none reproduces the initial ungrouped implementation used by the delivered
# checkpoint/refine workflow. Then run refine_grouped_core.tcl on routed.dcp.
# Grouping changes optimization cost only, never clocks or timing exceptions.
if {$argc < 2 || $argc > 3} {error "Expected common RTL directory, output directory, and optional before_place|none"}
set src [file normalize [lindex $argv 0]]
set out [file normalize [lindex $argv 1]]
set grouping_stage before_place
if {$argc == 3} {set grouping_stage [lindex $argv 2]}
if {$grouping_stage ni {before_place none}} {error "Grouping stage must be before_place or none"}
file mkdir $out
set snapshot [file join $out rtl]
file mkdir $snapshot
foreach source_file [glob [file join $src *.v]] {file copy -force $source_file $snapshot}
source [file join [file dirname [info script]] refine_timing.tcl]
source [file join [file dirname [info script]] report_internal_core.tcl]
set wrapper [file join $out linear14_core.v]
set f [open $wrapper w]
puts $f {module linear14_core(input clk,rst,uart_rx,input [1:0] trigger_in,output uart_tx,output reg [55:0] dac_data);
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
endmodule}
close $f
set_part xc7a35tfgg484-2
read_verilog [glob [file join $snapshot *.v]]
read_verilog $wrapper
synth_design -top linear14_core -mode out_of_context -part xc7a35tfgg484-2
create_clock -name core -period 5.000 [get_ports clk]
set_input_delay -clock core 1.000 [get_ports -filter {DIRECTION == IN && NAME != clk}]
set_output_delay -clock core 1.000 [all_outputs]
report_utilization -file [file join $out utilization_synth.rpt]
opt_design
if {$grouping_stage eq "before_place"} {ramp_group_internal}
place_design
phys_opt_design
report_timing -max_paths 20 -file [file join $out worst_paths_placed.rpt]
route_design
ramp_refine_internal
report_utilization -file [file join $out utilization.rpt]
report_timing_summary -report_unconstrained -file [file join $out timing.rpt]
report_timing -max_paths 20 -file [file join $out worst_paths.rpt]
report_drc -file [file join $out drc.rpt]
write_checkpoint -force [file join $out routed.dcp]
if {![ramp_report_internal $out]} {error "Four-channel 14-bit internal core timing not met"}
puts "RAMP_LINEAR14_INTERNAL_CORE_PASS: see result.json; external IO/MMCM/DAC timing not signed off"
exit 0
