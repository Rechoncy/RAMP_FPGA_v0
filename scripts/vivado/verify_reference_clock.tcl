# Read-only hardware verification of a generated project: simulation and optional synthesis.
# No hardware manager, JTAG programming, Flash operation or timing waiver is used.
if {$argc != 6} {error "Expected project testbench ref_MHz input_type boot_enabled synth_enabled"}
lassign $argv project testbench ref_mhz input_type boot_enabled synth_enabled
source [file join [file dirname [info script]] validate_reference_pins.tcl]
if {[catch {
 open_project [file normalize $project]
 add_files -fileset sim_1 [file normalize $testbench]
 set_property top tb_reference_clock [get_filesets sim_1]
 set_property generic {} [get_filesets sim_1]
 set_property xsim.simulate.runtime all [get_filesets sim_1]
 update_compile_order -fileset sim_1
 launch_simulation
 close_sim
 set directory [get_property DIRECTORY [current_project]]
 set f [open [file join $directory ramp_studio.sim sim_1 behav xsim simulate.log] r]
 set simulation [read $f]; close $f
 if {[string first "PASS REFERENCE_CLOCK" $simulation]<0} {error "Clock simulation failed"}
 if {$synth_enabled} {
  if {[get_property PROGRESS [get_runs synth_1]] ne "100%"} {
   launch_runs synth_1 -jobs 4
   wait_on_run synth_1
  }
  if {[get_property PROGRESS [get_runs synth_1]] ne "100%"} {error "Synthesis failed"}
  open_run synth_1
  ramp_validate_reference_pins $input_type [expr {$input_type eq "differential" ? {- -} : {R4}}]
  set prefix [string repeat core/ [expr {$boot_enabled+($input_type eq "differential")}]]
  set mmcm [get_cells ${prefix}u_clock/u_ip/inst/mmcm_adv_inst]
  if {[llength $mmcm]!=1 || [get_property REF_NAME $mmcm] ne "MMCME2_ADV"} {error "MMCM hierarchy incorrect"}
  set clock [get_clocks sys_clk]
  if {abs([get_property PERIOD $clock]-1000.0/$ref_mhz)>0.001} {error "Reference clock constraint incorrect"}
  foreach clock [get_clocks dac_clk_*] {
   if {abs([get_property PERIOD $clock]-5.0)>0.001} {error "DAC clock constraint is not 200 MHz"}
  }
  if {[llength [get_clocks dac_clk_*]]!=4} {error "Missing generated DAC clock constraints"}
  if {$input_type eq "differential"} {
   if {[llength [get_ports -quiet sys_clk]] || [llength [get_ports {sys_clk_p sys_clk_n}]]!=2} {error "Differential ports incorrect"}
   if {[llength [get_cells -hier -filter {REF_NAME == IBUFDS}]]!=1} {error "Expected exactly one differential input buffer"}
   foreach port [get_ports {sys_clk_p sys_clk_n}] {
    if {[get_property IOSTANDARD $port] ne "LVDS_25"} {error "Differential clock IO standard incorrect"}
   }
   if {[get_property CONFIG.PRIM_SOURCE [get_ips clk_core_ip]] ne "No_buffer"} {error "Clock IP has an extra input buffer"}
   # Check the package-pair guard against Vivado's xc7a35tfgg484 database.
   # These temporary test assignments are cleared; they are NOT board wiring.
   set_property PACKAGE_PIN R4 [get_ports sys_clk_p]
   set_property PACKAGE_PIN T4 [get_ports sys_clk_n]
   ramp_validate_reference_pins differential {R4 T4}
   if {![catch {
    set_property PACKAGE_PIN AA4 [get_ports sys_clk_n]
    ramp_validate_reference_pins differential {R4 AA4}
   } rejection]} {error "Mismatched pair accepted"}
   puts "EXPECTED REJECTION of mismatched pair: $rejection"
   reset_property PACKAGE_PIN [get_ports {sys_clk_p sys_clk_n}]
   if {![catch {
    set_property PACKAGE_PIN T4 [get_ports sys_clk_p]
    set_property PACKAGE_PIN R4 [get_ports sys_clk_n]
    ramp_validate_reference_pins differential {T4 R4}
   } rejection]} {error "Reversed P/N accepted"}
   puts "EXPECTED REJECTION of reversed P/N: $rejection"
   reset_property PACKAGE_PIN [get_ports {sys_clk_p sys_clk_n}]
  }
  report_clocks -file [file join $directory verified_clocks.rpt]
  report_io -file [file join $directory verified_io.rpt]
  write_checkpoint -force [file join $directory verified_synth.dcp]
 }
 close_project
 puts "PASS REFERENCE_CLOCK_PROJECT: $input_type $ref_mhz MHz, boot=$boot_enabled, synth=$synth_enabled"
} message]} {puts stderr "REFERENCE_CLOCK_FAILED: $message\n$::errorInfo"; exit 1}
exit 0
