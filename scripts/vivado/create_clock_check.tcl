# Isolated Clocking Wizard generation check. Does not modify the legacy project.
# Usage: vivado -mode batch -source create_clock_check.tcl \
#          -tclargs xc7a35tfgg484-2 50 200
# Validated with Vivado 2023.1 for xc7a35tfgg484-2, 50 MHz -> 200 MHz.
# IP generation only; no full-design synthesis or implementation is performed.
set script_dir [file dirname [file normalize [info script]]]
set repo_root [file normalize [file join $script_dir ../..]]

if {$argc > 3} {
    puts stderr "Usage: create_clock_check.tcl ?part? ?reference_MHz? ?core_MHz?"
    exit 1
}
set part xc7a35tfgg484-2
set reference_mhz 50
set core_mhz 200
if {$argc >= 1} {set part [lindex $argv 0]}
if {$argc >= 2} {set reference_mhz [lindex $argv 1]}
if {$argc >= 3} {set core_mhz [lindex $argv 2]}

foreach frequency [list $reference_mhz $core_mhz] {
    if {![string is double -strict $frequency]} {
        puts stderr "Clock frequencies must be positive finite numbers in MHz."
        exit 1
    }
    if {[catch {expr {$frequency > 0 && $frequency < 10000}} valid] || !$valid} {
        puts stderr "Clock frequencies must be positive finite numbers below 10000 MHz."
        exit 1
    }
}

# Unique output directory avoids overwriting earlier generated projects.
set output_dir [file join $repo_root build clock_check_[clock seconds]_[pid]]
if {[file exists $output_dir]} {
    puts stderr "Refusing to overwrite existing directory: $output_dir"
    exit 1
}

if {[catch {
    if {[llength [get_parts -quiet $part]] != 1} {
        error "Requested part is missing or ambiguous in this Vivado installation: $part"
    }
    create_project clock_check $output_dir -part $part
    set_property target_language Verilog [current_project]
    create_ip -name clk_wiz -vendor xilinx.com -library ip -version 6.0 \
        -module_name clk_core_ip
    set_property -dict [list \
        CONFIG.PRIM_IN_FREQ $reference_mhz \
        CONFIG.CLKOUT1_REQUESTED_OUT_FREQ $core_mhz \
        CONFIG.USE_RESET true \
        CONFIG.RESET_TYPE ACTIVE_HIGH \
        CONFIG.USE_LOCKED true] [get_ips clk_core_ip]
    generate_target all [get_ips clk_core_ip]
    report_property -all [get_ips clk_core_ip] \
        -file [file join $output_dir clock_ip_properties.txt]
    puts "CLOCK_IP_GENERATION_COMPLETE: $output_dir"
    puts "Generated IP only; full design synthesis, timing closure and board validation remain required."
    close_project
} diagnostic]} {
    puts stderr "CLOCK_IP_GENERATION_FAILED: $diagnostic"
    exit 1
}
exit 0
