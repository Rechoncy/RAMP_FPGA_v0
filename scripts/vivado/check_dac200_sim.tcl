# Run after build_studio.tcl has created a project for the current RTL.
if {$argc ni {1 3 4}} {puts stderr {Expected project path and optional DAC_COUNT TRIGGER_COUNT [DAC_BITS]}; exit 1}
set root [file normalize [file join [file dirname [info script]] ../..]]
if {[catch {
 open_project [file normalize [lindex $argv 0]]
 add_files -fileset sim_1 [file join $root tests/rtl/tb_dac200.v]
 set_property top tb_dac200 [get_filesets sim_1]
 if {$argc>=3} {set_property generic "DAC_COUNT=[lindex $argv 1] TRIGGER_COUNT=[lindex $argv 2]" [get_filesets sim_1]}
 if {$argc==4} {set_property generic "DAC_COUNT=[lindex $argv 1] TRIGGER_COUNT=[lindex $argv 2] DAC_BITS=[lindex $argv 3]" [get_filesets sim_1]}
 set_property xsim.simulate.runtime all [get_filesets sim_1]
 update_compile_order -fileset sim_1
 launch_simulation
 close_sim
 set log [file join [get_property DIRECTORY [current_project]] ramp_studio.sim sim_1 behav xsim simulate.log]
 if {![file exists $log]} {error "Simulation log missing: $log"}
 set handle [open $log r]
 set contents [read $handle]
 close $handle
 if {[string first "PASS DAC200" $contents]<0} {error "DAC200 simulation did not report PASS; inspect $log"}
 puts "DAC200_SIMULATION_VERIFIED: $log"
 close_project
} message]} {puts stderr "DAC200_SIMULATION_FAILED: $message"; exit 1}
exit 0
