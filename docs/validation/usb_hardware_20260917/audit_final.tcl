set out [file normalize [file dirname [info script]]]
open_checkpoint [file join $out routed_assumed.dcp]
report_bus_skew -file [file join $out bus_skew_final.rpt]
report_exceptions -file [file join $out exceptions_final.rpt]
report_clock_interaction -file [file join $out clock_interaction_final.rpt]
puts "AUDIT_ONLY_COMPLETE"
close_design
exit 0
