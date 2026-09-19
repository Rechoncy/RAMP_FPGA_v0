# Optional trailing arguments: DAC_COUNT TRIGGER_COUNT DAC_BITS SPI rate falling boot_dir|- clock_input clock_pin_P|- clock_pin_N|- uart_baud
set root [file normalize [file join [file dirname [info script]] ../..]]
source [file join [file dirname [info script]] refine_timing.tcl]
source [file join [file dirname [info script]] validate_reference_pins.tcl]
if {$argc ni {5 7 8 11 12 13 15 16 17 18}} { puts stderr {Expected: part ref_MHz xdc output_dir mode [DAC_COUNT TRIGGER_COUNT DAC_BITS SPI rate falling [boot_dir|- [single_ended|differential [clock_pin_P|- clock_pin_N|- [uart_baud [interfaces_dir|- [core_MHz]]]]]]]}; exit 1 }
lassign $argv part ref_mhz xdc out mode
set clock_input single_ended
if {$argc>=13} {set clock_input [lindex $argv 12]}
if {$clock_input ni {single_ended differential}} {puts stderr "Invalid reference clock input type"; exit 1}
set reference_pins {}
if {$argc>=15} {set reference_pins [lrange $argv 13 14]}
if {$clock_input eq "differential" && $argc<15} {puts stderr "Differential input requires the original P/N pin arguments (use - for an unassigned draft pin)."; exit 1}
if {$clock_input eq "single_ended" && $argc>=15} {set reference_pins [lrange $reference_pins 0 0]}
set uart_baud 115200
if {$argc>=16} {set uart_baud [lindex $argv 15]}
if {$uart_baud ni {9600 19200 38400 57600 115200 230400 460800 921600 1000000}} {puts stderr "Unsupported UART baud rate (maximum 1000000)"; exit 1}
if {![string is double -strict $ref_mhz] || [catch {expr {$ref_mhz>=10 && $ref_mhz<=500}} valid_frequency] || !$valid_frequency} {puts stderr "Reference clock must be 10..500 MHz"; exit 1}
set boot_dir ""
if {$argc>=12 && [lindex $argv 11] ne "-"} {set boot_dir [lindex $argv 11]}
set dac_count 2
set trigger_count 2
set dac_bits 10
if {$argc>=7} {lassign [lrange $argv 5 6] dac_count trigger_count}
if {$argc>=8} {set dac_bits [lindex $argv 7]}
if {$dac_bits ni {10 12 14}} {puts stderr "DAC_BITS must be 10, 12, or 14"; exit 1}
foreach count [list $dac_count $trigger_count] {
 if {![string is integer -strict $count] || $count<1 || $count>8} {puts stderr "Channel counts must be 1..8"; exit 1}
}
set flash_interface SPIx1
set flash_rate 16
set flash_falling 1
if {$argc>=11} {lassign [lrange $argv 8 10] flash_interface flash_rate flash_falling}
if {$flash_interface ni {SPIx1 SPIx2 SPIx4} || $flash_rate ni {3 6 9 12 16 22 26 33 40 50 66} || $flash_falling ni {0 1}} {error "Invalid Flash boot settings"}
set out [file normalize $out]
set interfaces_dir ""
if {$argc>=17 && [lindex $argv 16] ne "-"} {set interfaces_dir [file normalize [lindex $argv 16]]}
set core_mhz 200
if {$argc>=18} {set core_mhz [lindex $argv 17]}
if {$core_mhz ni {100 125 200}} {error "Output rate must be 100, 125 or 200 MSPS"}
set core_hz [expr {$core_mhz*1000000}]
set build_jobs [expr {$interfaces_dir eq "" ? 4:1}]
if {[file exists $out]} { puts stderr "Output directory already exists: $out"; exit 1 }
if {$mode ni {generate synth check implement}} {puts stderr "Invalid build mode"; exit 1}
if {[catch {
 create_project ramp_studio $out -part $part
 set_property target_language Verilog [current_project]
 set_property STEPS.PHYS_OPT_DESIGN.IS_ENABLED true [get_runs impl_1]
 add_files [glob [file join $root rtl/common/*.v]]
 add_files [glob [file join $root rtl/platform/xilinx_7series/*.v]]
 add_files -fileset constrs_1 [file normalize $xdc]
 set_property top [expr {$clock_input eq "differential" ? "ramp_top_diff" : "ramp_top"}] [current_fileset]
 set_property generic "DAC_COUNT=$dac_count TRIGGER_COUNT=$trigger_count DAC_BITS=$dac_bits UART_BAUD=$uart_baud CORE_HZ=$core_hz" [current_fileset]
 if {$boot_dir ne ""} {
  set boot_dir [file normalize $boot_dir]
  add_files [file join $boot_dir ramp_boot_top.v]
  set boot_memory [glob [file join $boot_dir *.mem*]]
  add_files $boot_memory
  set_property file_type {Memory Initialization Files} [get_files $boot_memory]
  set_property generic {} [current_fileset]
  set_property top ramp_boot_top [current_fileset]
 }
 if {$interfaces_dir ne ""} {
  add_files [file join $interfaces_dir ramp_extended_top.v]
  set_property generic {} [current_fileset]
  set_property top [expr {$boot_dir ne "" ? "ramp_boot_top" : "ramp_extended_top"}] [current_fileset]
  # Tcl flow control is not legal in an XDC reader. Run the constraint script
  # against a linked design so all Gray-pointer endpoints and clocks exist.
  # SYNTH_DESIGN.POST still runs before the IP checkpoints are linked. Apply
  # after open_run below, then reapply from the implementation pre-opt hook.
  set usb_cdc_hook [file join $root scripts/vivado/usb_cdc.tcl]
  set_property STEPS.OPT_DESIGN.TCL.PRE $usb_cdc_hook [get_runs impl_1]
 }
 create_ip -name clk_wiz -vendor xilinx.com -library ip -version 6.0 -module_name clk_core_ip
 # The differential wrapper already instantiates IBUFDS. No_buffer prevents
 # an invalid second physical input buffer inside Clocking Wizard.
 set input_buffer [expr {$clock_input eq "differential" ? "No_buffer" : "Single_ended_clock_capable_pin"}]
 set_property -dict [list CONFIG.PRIM_SOURCE $input_buffer CONFIG.PRIM_IN_FREQ $ref_mhz CONFIG.CLKOUT1_REQUESTED_OUT_FREQ $core_mhz \
  CONFIG.CLKOUT2_USED false \
  CONFIG.USE_LOCKED true CONFIG.USE_RESET true CONFIG.RESET_TYPE ACTIVE_HIGH] [get_ips clk_core_ip]
 generate_target all [get_ips clk_core_ip]
 update_compile_order -fileset sources_1
 if {$mode ne "generate"} {
  launch_runs synth_1 -jobs $build_jobs
  wait_on_run synth_1
  if {[get_property PROGRESS [get_runs synth_1]] ne "100%"} {error "Synthesis failed"}
  open_run synth_1
  if {$interfaces_dir ne ""} {source $usb_cdc_hook}
  report_utilization -file [file join $out utilization_synth.rpt]
  ramp_validate_reference_pins $clock_input $reference_pins
  report_timing_summary -file [file join $out timing_synth.rpt]
  report_clocks -file [file join $out clocks.rpt]
  report_cdc -file [file join $out cdc_synth.rpt]
 }
 if {$mode eq "implement"} {
  close_design
  launch_runs impl_1 -to_step route_design -jobs $build_jobs
  wait_on_run impl_1
  if {[get_property PROGRESS [get_runs impl_1]] ne "100%"} {error "Implementation failed"}
  open_run impl_1
  report_timing_summary -report_unconstrained -file [file join $out timing_before_refinement.rpt]
  report_timing -max_paths 10 -file [file join $out setup_before_refinement.rpt]
  ramp_refine_timing
  report_utilization -file [file join $out utilization_routed.rpt]
  report_timing_summary -report_unconstrained -file [file join $out timing_routed.rpt]
  report_drc -file [file join $out drc_routed.rpt]
  report_cdc -file [file join $out cdc_routed.rpt]
  report_io -file [file join $out io.rpt]
  # Do not generate a programming image while setup or hold timing fails.
  foreach delay {max min} {
   set path [get_timing_paths -delay_type $delay -max_paths 1]
   if {[llength $path]==0 || [get_property SLACK $path]<0} {error "$delay timing not met; no bitstream generated"}
  }
  set_property CONFIG_MODE $flash_interface [current_design]
  set_property BITSTREAM.CONFIG.SPI_BUSWIDTH [string index $flash_interface end] [current_design]
  set_property BITSTREAM.CONFIG.CONFIGRATE $flash_rate [current_design]
  set_property BITSTREAM.CONFIG.SPI_FALL_EDGE [expr {$flash_falling ? "Yes" : "No"}] [current_design]
  write_bitstream [file join $out ramp_studio.bit]
 }
 if {$mode eq "check"} {
  set reports [file join $out core_route_check]
  file mkdir $reports
  opt_design
  place_design
  phys_opt_design
  route_design
  report_timing_summary -report_unconstrained -file [file join $reports timing_before_refinement.rpt]
  report_timing -max_paths 10 -file [file join $reports setup_before_refinement.rpt]
  ramp_refine_timing
  report_timing_summary -report_unconstrained -file [file join $reports timing.rpt]
  report_cdc -file [file join $reports cdc.rpt]
  report_drc -file [file join $reports drc.rpt]
  report_utilization -file [file join $reports utilization.rpt]
  write_checkpoint -force [file join $reports routed.dcp]
  foreach delay {max min} {
   set path [get_timing_paths -delay_type $delay -max_paths 1]
   puts "CORE $delay SLACK: [get_property SLACK $path]"
   if {[llength $path]==0 || [get_property SLACK $path]<0} {error "Core $delay timing not met"}
  }
  puts "Core paths passed. Incomplete DAC/board constraints still prevent full-system signoff. No bitstream written."
 }
 puts "RAMP_BUILD_SUCCESS: $out ($mode)"
 close_project
} message]} { puts stderr "RAMP_BUILD_FAILED: $message"; exit 1 }
exit 0
