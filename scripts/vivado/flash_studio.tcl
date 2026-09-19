# Standalone SPI configuration-memory workflow. The job config contains quoted data only.
if {$argc != 1} {puts stderr "Expected job configuration";exit 1}
source [lindex $argv 0]
file mkdir $cfg(output)
proc exact_object {objects name description} {
 set i [lsearch -exact $objects $name]
 if {$i < 0} {error "$description not found: $name"}
 return [lindex $objects $i]
}
proc boot_status {dev} {
 global cfg
 refresh_hw_device -update_hw_probes false $dev
 set f [open [file join $cfg(output) boot_status.txt] w]
 foreach p {PART REGISTER.USR_ACCESS REGISTER.CONFIG_STATUS.BIT00_CRC_ERROR REGISTER.CONFIG_STATUS.BIT14_DONE_PIN} {
  set value [get_property $p $dev]
  puts $f "$p=$value";puts "$p=$value"
 }
 close $f
 if {[get_property REGISTER.CONFIG_STATUS.BIT14_DONE_PIN $dev] ne "1" || [get_property REGISTER.CONFIG_STATUS.BIT00_CRC_ERROR $dev] ne "0"} {error "FPGA DONE/CRC check failed"}
 if {$cfg(expected_usr_access) ne ""} {
  scan [get_property REGISTER.USR_ACCESS $dev] %x actual
  scan $cfg(expected_usr_access) %x expected
  if {$actual != $expected} {error "Firmware USR_ACCESS does not match expected value"}
 }
 puts "DONE/CRC check passed. Cold power-cycle and external DAC measurements are separate checks."
}
set opened 0
set rc [catch {
 if {$cfg(operation) in {generate program}} {
  if {$cfg(interface) ni {SPIx1 SPIx2 SPIx4}} {error "Invalid SPI interface"}
  foreach format {MCS BIN} {
   write_cfgmem -force -format $format -size $cfg(size_mib) -interface $cfg(interface) -loadbit [list up 0x0 $cfg(bitfile)] [file join $cfg(output) firmware.[string tolower $format]]
  }
  puts "Images generated at boot address 0. Existing bitstream configuration settings are not rewritten."
 }
 if {$cfg(operation) eq "scan"} {
  foreach part [get_cfgmem_parts -quiet *spi*] {puts "RAMP_CFGMEM\t$part"}
 }
 if {$cfg(operation) ne "generate"} {
  open_hw_manager
  connect_hw_server -url $cfg(server)
  if {$cfg(operation) eq "scan"} {
   foreach target [get_hw_targets] {
    open_hw_target $target
    foreach dev [get_hw_devices] {puts "RAMP_TARGET\t$target\t$dev"}
    close_hw_target
   }
  } else {
   set target [exact_object [get_hw_targets] $cfg(target) "JTAG target"]
   open_hw_target $target;set opened 1
   set dev [exact_object [get_hw_devices] $cfg(device) "FPGA device"]
   current_hw_device $dev
   if {$cfg(operation) in {program temporary}} {
    set device_part [string tolower [get_property PART $dev]]
    regsub {^xc} $device_part {} device_part
    if {![string match "${device_part}*" $cfg(bit_part)]} {error "Bitstream part does not match selected FPGA; no hardware programming was started"}
   }
   if {$cfg(operation) eq "program"} {
    set part [exact_object [get_cfgmem_parts] $cfg(cfgmem_part) "Flash model"]
    if {[get_property MEM_DENSITY $part] != $cfg(size_mib)*8} {error "Flash model density does not match capacity setting"}
    set width [string index $cfg(interface) end]
    if {[lsearch -exact [split [get_property DATA_WIDTH $part] _] x$width]<0} {error "Flash model does not support selected SPI width"}
    create_hw_cfgmem -hw_device $dev $part
    set mem [get_property PROGRAM.HW_CFGMEM $dev]
    foreach p {PROGRAM.ERASE PROGRAM.BLANK_CHECK PROGRAM.CFG_PROGRAM PROGRAM.VERIFY} {set_property $p 0 $mem}
    create_hw_bitstream -hw_device $dev [get_property PROGRAM.HW_CFGMEM_BITFILE $dev]
    program_hw_devices $dev
    if {$cfg(backup)} {
     set backup [file join $cfg(output) previous_flash.bin]
     readback_hw_cfgmem -all -format bin -file $backup $mem
     if {[file size $backup] != $cfg(size_mib)*1024*1024} {error "Backup size differs from selected capacity; Flash erase was not started"}
     puts "Full Flash backup completed: $backup"
    }
    set_property PROGRAM.FILES [list [file join $cfg(output) firmware.mcs]] $mem
    set_property PROGRAM.ADDRESS_RANGE use_file $mem
    foreach p {PROGRAM.ERASE PROGRAM.BLANK_CHECK PROGRAM.CFG_PROGRAM PROGRAM.VERIFY} {set_property $p 1 $mem}
    program_hw_cfgmem $mem
    foreach p {PROGRAM.ERASE PROGRAM.BLANK_CHECK PROGRAM.CFG_PROGRAM PROGRAM.VERIFY} {set_property $p 0 $mem}
    set expected_file [file join $cfg(output) firmware.bin]
    set actual_file [file join $cfg(output) flash_readback.bin]
    readback_hw_cfgmem -offset 0 -datacount [file size $expected_file] -format bin -file $actual_file $mem
    set f [open $expected_file rb];set expected [read $f];close $f
    set f [open $actual_file rb];set actual [read $f];close $f
    if {$actual ne $expected} {error "Flash byte comparison failed"}
    puts "FLASH_PROGRAM_AND_BYTE_VERIFY_PASSED"
    puts "FPGA currently contains the Flash programming helper. Use Boot from Flash next."
   } elseif {$cfg(operation) eq "temporary"} {
    set_property PROGRAM.FILE $cfg(bitfile) $dev
    program_hw_devices $dev
    boot_status $dev
   } elseif {$cfg(operation) eq "boot"} {
    boot_hw_device -timeout 30 $dev
    boot_status $dev
   } elseif {$cfg(operation) eq "status"} {
    puts "READ_ONLY_STATUS_NO_BITSTREAM_LOADED"
    boot_status $dev
   } else {error "Unknown Flash operation"}
  }
 }
} message options]
if {$opened} {catch {close_hw_target}}
catch {disconnect_hw_server}
catch {close_hw_manager}
if {$rc} {puts stderr "FLASH_FAILED: $message";exit 1}
puts "RAMP_FLASH_SUCCESS: $cfg(operation)"
exit 0
