# Read the frozen, previously validated routed checkpoint. Add explicitly
# assumed DAC timing for this diagnostic; do not change existing constraints.
set root [file normalize [file join [file dirname [info script]] ../..]]
set out [file dirname [info script]]
set checkpoint [file join $root build/usb_uart_final_20260916/vivado_iob_final/core_route_check/routed.dcp]
proc worst_slack {delay} {
 set worst 1e9
 foreach group [get_path_groups] {
  foreach path [get_timing_paths -quiet -group $group -delay_type $delay -max_paths 1] {
   set slack [get_property SLACK $path]
   if {$slack < $worst} {set worst $slack}
  }
 }
 if {$worst == 1e9} {error "No constrained $delay path"}
 return $worst
}
if {[catch {
 open_checkpoint $checkpoint
 if {[get_property PART [current_design]] ne "xc7a35tfgg484-2"} {error "Part mismatch"}
 if {[llength [get_ports]] != 66} {error "Expected 66 physical ports for USB+UART/4x10/2 triggers"}
 for {set i 0} {$i<4} {incr i} {
  set name dac_clk_[expr {$i+1}]
  if {[get_property PERIOD [get_clocks $name]] != 5.000} {error "DAC clock mismatch"}
  set bus {}
  for {set j 0} {$j<10} {incr j} {lappend bus [format {dac_data[%d]} [expr {$i*10+$j}]]}
  set_output_delay -clock [get_clocks $name] -min -0.500 [get_ports $bus]
  set_output_delay -clock [get_clocks $name] -max 1.000 [get_ports $bus]
 }
 report_timing_summary -report_unconstrained -file [file join $out timing_assumed_initial.rpt]
 report_timing -to [get_ports {dac_data[*]}] -max_paths 20 -file [file join $out dac_setup_initial.rpt]
 report_timing -to [get_ports {dac_data[*]}] -delay_type min -max_paths 20 -file [file join $out dac_hold_initial.rpt]
 puts "ASSUMED_INITIAL_SETUP=[worst_slack max]; HOLD=[worst_slack min]"
 if {[worst_slack max]<0 || [worst_slack min]<0} {
  source [file join $root scripts/vivado/refine_timing.tcl]
  ramp_refine_timing
 }
 set_property BITSTREAM.CONFIG.USR_ACCESS 0x09171701 [current_design]
 report_timing_summary -report_unconstrained -file [file join $out timing_assumed_final.rpt]
 report_timing -to [get_ports {dac_data[*]}] -max_paths 40 -file [file join $out dac_setup_final.rpt]
 report_timing -to [get_ports {dac_data[*]}] -delay_type min -max_paths 40 -file [file join $out dac_hold_final.rpt]
 report_drc -file [file join $out drc_final.rpt]
 report_cdc -details -file [file join $out cdc_final.rpt]
 report_utilization -file [file join $out utilization_final.rpt]
 report_io -file [file join $out io_final.rpt]
 write_checkpoint -force [file join $out routed_assumed.dcp]
 write_xdc -force [file join $out implemented_assumed.xdc]
 set setup [worst_slack max];set hold [worst_slack min]
 if {$setup<0 || $hold<0} {error "Diagnostic timing failed: setup=$setup / hold=$hold. No bitstream."}
 set f [open [file join $out timing_assumed_final.rpt] r];set text [read $f];close $f
 set fields {};set in_summary 0
 foreach line [split $text \n] {
  if {[string match {*Design Timing Summary*} $line]} {set in_summary 1;continue}
  if {!$in_summary} {continue}
  set parts [regexp -all -inline {\S+} $line]
  if {[llength $parts]!=12} {continue}
  set numeric 1;foreach part $parts {if {![string is double -strict $part]} {set numeric 0}}
  if {$numeric} {set fields $parts;break}
 }
 if {![llength $fields] || [lindex $fields 8]<0 || [lindex $fields 10]!=0} {error "Missing/failed pulse-width timing"}
 foreach kind {no_clock constant_clock unconstrained_internal_endpoints generated_clocks loops multiple_clock partial_input_delay partial_output_delay latch_loops} {
  if {![regexp [format {checking %s \(([0-9]+)\)} $kind] $text -> count] || $count != 0} {error "Timing coverage failed: $kind"}
 }
 if {[llength [get_drc_violations -quiet -filter {SEVERITY == Error}]]} {error "DRC errors"}
 set bit [file join $out ramp_usb_uart_4x10_09171701.bit]
 write_bitstream $bit
 set f [open [file join $out build_result.json] w]
 puts $f [format {{"result":"passed with assumed DAC timing","usr_access":"09171701","setup_ns":%s,"hold_ns":%s,"pulse_width_ns":%s,"dac_sample_edge":"rising","dac_min_ns":-0.5,"dac_max_ns":1.0,"external_dac_parameters_verified":false,"fpga_programmed":false,"flash_programmed":false}} $setup $hold [lindex $fields 8]]
 close $f
 puts "DIAGNOSTIC_BITSTREAM_READY: $bit"
 close_design
} msg opts]} {
 set f [open [file join $out build_failure.txt] w];puts $f "$msg\n[dict get $opts -errorinfo]";close $f
 puts stderr "DIAGNOSTIC_FAILED: $msg";exit 1
}
exit 0
