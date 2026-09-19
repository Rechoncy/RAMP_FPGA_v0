# Route an existing synthesized Studio project for internal timing analysis only.
# Missing board IO constraints remain visible; this script never writes a bitstream.
if {$argc != 1} {puts stderr "Expected synthesized project path"; exit 1}
if {[catch {
 open_project [file normalize [lindex $argv 0]]
 set out [file join [get_property DIRECTORY [current_project]] core_route_check]
 file mkdir $out
 open_run synth_1
 opt_design
 place_design
 phys_opt_design
 route_design
 report_timing_summary -report_unconstrained -file [file join $out timing.rpt]
 report_cdc -file [file join $out cdc.rpt]
 report_drc -file [file join $out drc.rpt]
 report_utilization -file [file join $out utilization.rpt]
 write_checkpoint -force [file join $out routed.dcp]
 foreach kind {max min} {
  puts "$kind SLACK: [get_property SLACK [get_timing_paths -delay_type $kind -max_paths 1]]"
 }
 puts "CORE_ROUTE_CHECK_COMPLETE: $out; DAC IO timing and pin completeness still require board configuration."
 close_project
} message]} {puts stderr "CORE_ROUTE_CHECK_FAILED: $message"; exit 1}
exit 0
