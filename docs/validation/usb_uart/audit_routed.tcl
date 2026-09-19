set_param general.maxThreads 2
set out [file normalize [lindex $argv 0]]
set reports [file join $out core_route_check]
open_checkpoint [file join $reports routed.dcp]
report_timing_summary -report_unconstrained -file [file join $reports timing.rpt]
report_timing -delay_type max -max_paths 10 -file [file join $reports setup_paths.rpt]
report_timing -delay_type min -max_paths 10 -file [file join $reports hold_paths.rpt]
report_timing -to [get_ports usb_rd_n] -max_paths 5 -file [file join $reports usb_rd_paths.rpt]
report_bus_skew -file [file join $reports bus_skew.rpt]
report_cdc -details -file [file join $reports cdc_details.rpt]
check_timing -verbose -file [file join $reports check_timing.rpt]
report_drc -file [file join $reports drc.rpt]
report_utilization -file [file join $reports utilization.rpt]
report_exceptions -file [file join $reports exceptions.rpt]
foreach port {usb_rd_n usb_wr_n usb_oe_n} {
 puts "USB_IO_PROPERTY: $port SLEW=[get_property SLEW [get_ports $port]] DRIVE=[get_property DRIVE [get_ports $port]]"
}
foreach cell [get_cells -hier -quiet -filter {NAME =~ *drive_data_reg*}] {
 puts "USB_DIRECTION_REGISTER: $cell REF=[get_property REF_NAME $cell] LOC=[get_property LOC $cell] BEL=[get_property BEL $cell] IOB=[get_property IOB $cell]"
}
close_design
exit
