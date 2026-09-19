# Read/verify all internal synchronous timing checks, including BRAM/DSP and
# control pins such as CE and synchronous reset. No timing paths are disabled.
proc ramp_internal_cells {} {
 return [lsort -unique [concat [all_registers] [get_cells -hier -filter {REF_NAME =~ RAMB* || REF_NAME =~ DSP48*}]]]
}
proc ramp_internal_path {delay} {
 set cells [ramp_internal_cells]
 set paths {}
 foreach group [get_path_groups] {
  set paths [concat $paths [get_timing_paths -group $group -from $cells -to $cells -delay_type $delay -max_paths 1]]
 }
 return [ramp_worst_path $paths]
}
proc ramp_global_path {delay} {
 set paths {}
 foreach group [get_path_groups] {
  set paths [concat $paths [get_timing_paths -group $group -delay_type $delay -max_paths 1]]
 }
 return [ramp_worst_path $paths]
}
# get_timing_paths may return a worst path per group. Explicitly select the
# minimum slack so adding an optimization group cannot change PASS semantics.
proc ramp_worst_path {paths} {
 set worst ""
 foreach path $paths {
  if {$worst eq "" || [get_property SLACK $path] < [get_property SLACK $worst]} {set worst $path}
 }
 return $worst
}
proc ramp_group_internal {} {
 set cells [ramp_internal_cells]
 # This only separates physical-optimization cost groups. All clocks, IO
 # delays, max/min checks and paths remain constrained exactly as before.
 group_path -name core_internal -from $cells -to $cells
}
proc ramp_refine_internal {} {
 global out
 for {set attempt 0} {$attempt<2} {incr attempt} {
  set failing 0
  foreach delay {max min} {
   set path [ramp_internal_path $delay]
   if {[llength $path]==0} {error "No internal $delay path"}
   if {[get_property SLACK $path]<0} {set failing 1}
  }
  set cells [ramp_internal_cells]
  report_timing -from $cells -to $cells -max_paths 20 -file [file join $out internal_setup_before_refine${attempt}.rpt]
  if {!$failing} {return}
  puts "RAMP_INTERNAL_TIMING_REFINEMENT: pass [expr {$attempt+1}]"
  phys_opt_design -directive AggressiveExplore
  route_design -directive Explore
 }
}
proc ramp_report_internal {out} {
 set cells [ramp_internal_cells]
 set memories [get_cells -hier -filter {REF_NAME =~ RAMB*}]
 set dsps [get_cells -hier -filter {REF_NAME =~ DSP48*}]
 set bram_launch [get_timing_paths -from $memories -to $cells -max_paths 1]
 set bram_capture [get_timing_paths -from $cells -to $memories -max_paths 1]
 set dsp_paths [get_timing_paths -from $dsps -to $cells -max_paths 1]
 if {[llength $bram_launch]==0 || [llength $bram_capture]==0 || [llength $dsp_paths]==0} {error "Internal timing selection missed BRAM/DSP launch or capture"}
 report_timing -from $cells -to $cells -max_paths 30 -file [file join $out internal_setup.rpt]
 report_timing -from $cells -to $cells -delay_type min -max_paths 30 -file [file join $out internal_hold.rpt]
 report_timing -from $memories -to $cells -max_paths 4 -file [file join $out internal_bram_launch.rpt]
 report_timing -from $cells -to $memories -max_paths 4 -file [file join $out internal_bram_capture.rpt]
 report_timing -from $dsps -to $cells -max_paths 4 -file [file join $out internal_dsp.rpt]
 set setup [get_property SLACK [ramp_internal_path max]]
 set hold [get_property SLACK [ramp_internal_path min]]
 set full_setup [get_property SLACK [ramp_global_path max]]
 set full_hold [get_property SLACK [ramp_global_path min]]
 set f [open [file join $out result.json] w]
 puts $f "{\"part\":\"xc7a35tfgg484-2\",\"clock_hz\":200000000,\"dac_count\":4,\"dac_bits\":14,\"runs_per_channel_per_bank\":2048,\"descriptor_bits\":46,\"scope\":\"all internal synchronous register/BRAM/DSP checks (including CE and synchronous R) with routed BUFG tree; full OOC port paths preserved but not signed off; excludes MMCM and external DAC/UART/reset timing\",\"setup_slack_ns\":$setup,\"hold_slack_ns\":$hold,\"internal_setup_slack_ns\":$setup,\"internal_hold_slack_ns\":$hold,\"full_setup_slack_ns\":$full_setup,\"full_hold_slack_ns\":$full_hold,\"internal_cells\":[llength $cells],\"bram_cells\":[llength $memories],\"dsp_cells\":[llength $dsps]}"
 close $f
 return [expr {$setup>=0 && $hold>=0}]
}
