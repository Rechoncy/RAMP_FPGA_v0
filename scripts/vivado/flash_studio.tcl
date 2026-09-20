# Standalone SPI configuration-memory workflow. The job config contains quoted data only.
if {$argc != 1} {puts stderr "Expected job configuration";exit 1}
source [lindex $argv 0]
# Backward-compatible defaults for saved .bit jobs.
if {![info exists cfg(image_source)]} {set cfg(image_source) bit}
if {$cfg(image_source) ni {bit image}} {puts stderr "Invalid image source";exit 1}
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
proc check_image_device {dev expected} {
 # PART comes from the selected JTAG device, not the user's board JSON. Obtain
 # its IDCODE from Vivado's part database (same ID across packages/speed grades).
 set device_part [string tolower [get_property PART $dev]]
 if {![string match xc7* $device_part]} {error "Existing images currently support Xilinx 7 series only"}
 set parts [get_parts -quiet -filter [list DEVICE == $device_part]]
 if {![llength $parts]} {error "Cannot determine selected FPGA IDCODE; no programming was started"}
 set ids [lsort -unique [get_property IDCODE $parts]]
 if {[llength $ids]!=1 || [scan [lindex $ids 0] %x actual]!=1 || [scan $expected %x wanted]!=1} {error "Invalid FPGA IDCODE; no programming was started"}
 if {($actual & 0x0fffffff)!=($wanted & 0x0fffffff)} {error "Image IDCODE does not match selected FPGA; no hardware programming was started"}
 puts "Image IDCODE matches selected FPGA: $device_part"
}
set opened 0
set rc [catch {
 if {$cfg(operation) in {generate temporary} && $cfg(image_source) ne "bit"} {error "This operation requires a .bit input"}
 if {$cfg(operation) in {generate program} && $cfg(image_source) eq "bit"} {
  if {$cfg(interface) ni {SPIx1 SPIx2 SPIx4}} {error "Invalid SPI interface"}
  foreach format {MCS BIN} {
   write_cfgmem -force -format $format -size $cfg(size_mib) -interface $cfg(interface) -loadbit [list up 0x0 $cfg(bitfile)] [file join $cfg(output) firmware.[string tolower $format]]
  }
  puts "Images generated at boot address 0. Existing bitstream configuration settings are not rewritten."
  set cfg(program_file) [file join $cfg(output) firmware.mcs]
  set cfg(verify_file) [file join $cfg(output) firmware.bin]
 }
 if {$cfg(operation) eq "program" && $cfg(image_source) eq "image"} {
  if {![file isfile $cfg(program_file)] || ![file isfile $cfg(verify_file)] || [file size $cfg(verify_file)]<1 || [file size $cfg(verify_file)]>$cfg(size_mib)*1024*1024} {error "Missing or oversized inspected image"}
  puts "Using inspected existing image directly; no write_cfgmem, synthesis or implementation."
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
   if {$cfg(operation) eq "program" && $cfg(image_source) eq "image"} {
    check_image_device $dev $cfg(image_idcode)
   } elseif {$cfg(operation) in {program temporary}} {
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
    set_property PROGRAM.FILES [list $cfg(program_file)] $mem
    if {[string tolower [file extension $cfg(program_file)]] eq ".bin"} {set_property PROGRAM.BIN_OFFSET 0 $mem}
    set_property PROGRAM.ADDRESS_RANGE use_file $mem
    foreach p {PROGRAM.ERASE PROGRAM.BLANK_CHECK PROGRAM.CFG_PROGRAM PROGRAM.VERIFY} {set_property $p 1 $mem}
    program_hw_cfgmem $mem
    foreach p {PROGRAM.ERASE PROGRAM.BLANK_CHECK PROGRAM.CFG_PROGRAM PROGRAM.VERIFY} {set_property $p 0 $mem}
    set expected_file $cfg(verify_file)
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
