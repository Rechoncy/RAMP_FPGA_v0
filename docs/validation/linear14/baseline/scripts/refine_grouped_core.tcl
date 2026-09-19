# Revisit an existing routed checkpoint with the SAME timing constraints.
# argv: checkpoint; matching RTL snapshot; new output directory.
if {$argc != 3} {error "Expected routed DCP, matching common RTL snapshot, and output directory"}
set checkpoint [file normalize [lindex $argv 0]]
set src [file normalize [lindex $argv 1]]
set out [file normalize [lindex $argv 2]]
file mkdir $out
file mkdir [file join $out rtl]
foreach source_file [glob [file join $src *.v]] {file copy -force $source_file [file join $out rtl]}
set wrapper [file join [file dirname $checkpoint] linear14_core.v]
if {![file exists $wrapper]} {error "Checkpoint wrapper is required for reproducible timing evidence"}
file copy -force $wrapper [file join $out linear14_core.v]
source [file join [file dirname [info script]] report_internal_core.tcl]
open_checkpoint $checkpoint
ramp_group_internal
# Preserve the inherited timing constraints for independent inspection.
write_xdc -force [file join $out inherited_constraints.xdc]
ramp_refine_internal
report_utilization -file [file join $out utilization.rpt]
report_timing_summary -report_unconstrained -file [file join $out timing.rpt]
report_timing -max_paths 20 -file [file join $out worst_paths.rpt]
report_drc -file [file join $out drc.rpt]
write_checkpoint -force [file join $out routed.dcp]
if {![ramp_report_internal $out]} {error "Grouped internal core timing not met"}
puts "RAMP_GROUPED_INTERNAL_CORE_PASS: IO/MMCM/DAC timing not signed off"
exit 0
