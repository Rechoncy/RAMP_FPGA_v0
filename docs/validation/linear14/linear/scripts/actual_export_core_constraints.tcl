# Read-only evidence export. This does not optimize or change the checkpoint.
if {$argc != 1} {error "Expected final core evidence directory"}
set out [file normalize [lindex $argv 0]]
file copy -force [info script] [file join $out actual_export_core_constraints.tcl]
open_checkpoint [file join $out routed.dcp]
set clocks [get_clocks]
if {[llength $clocks] != 1 || [get_property PERIOD $clocks] != 5.000} {
 error "Expected the preserved single 200 MHz (5 ns) core clock"
}
write_xdc -force [file join $out inherited_constraints.xdc]
puts "RAMP_EVIDENCE_CONSTRAINTS_EXPORTED: $clocks, period [get_property PERIOD $clocks] ns"
exit 0
