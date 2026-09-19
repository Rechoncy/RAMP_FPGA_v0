# Read-only cold/warm boot check for the validated XC7A35T board.
# Never loads a bitstream: doing so would conceal a failed Flash boot.
set root [file normalize [file join [file dirname [info script]] ../..]]
set output [file join $root build/flash_boot_20260911/cold_boot_status.txt]
if {[llength $argv] > 0} {set output [lindex $argv 0]}
open_hw_manager
connect_hw_server -url localhost:3121
set targets [get_hw_targets -quiet *210512180081]
if {[llength $targets] != 1} {error "Expected JTAG target missing"}
open_hw_target [lindex $targets 0]
set dev [get_hw_devices xc7a35t_0]
refresh_hw_device -update_hw_probes false $dev
set out [open $output w]
puts $out "CHECK_TIME=[clock format [clock seconds] -format {%Y-%m-%d %H:%M:%S}]"
puts $out "READ_ONLY_NO_BITSTREAM_LOADED=1"
foreach p {PART REGISTER.USR_ACCESS REGISTER.CONFIG_STATUS REGISTER.BOOT_STATUS REGISTER.CONFIG_STATUS.BIT00_CRC_ERROR REGISTER.CONFIG_STATUS.BIT12_INIT_B_PIN REGISTER.CONFIG_STATUS.BIT13_DONE_INTERNAL_SIGNAL_STATUS REGISTER.CONFIG_STATUS.BIT14_DONE_PIN REGISTER.CONFIG_STATUS.BIT15_IDCODE_ERROR} {
 set value [get_property $p $dev]
 puts "$p=$value"
 puts $out "$p=$value"
}
set ok [expr {[get_property REGISTER.USR_ACCESS $dev] eq "09111930" && [get_property REGISTER.CONFIG_STATUS.BIT14_DONE_PIN $dev] eq "1" && [get_property REGISTER.CONFIG_STATUS.BIT00_CRC_ERROR $dev] eq "0"}]
puts $out "EXPECTED_BOOT_FIRMWARE_PRESENT=$ok"
close $out
close_hw_target
disconnect_hw_server
close_hw_manager
if {!$ok} {error "Expected Flash boot firmware not running; inspect status"}
puts "EXPECTED_FLASH_FIRMWARE_RUNNING_NO_JTAG_PROGRAMMING"
exit 0
