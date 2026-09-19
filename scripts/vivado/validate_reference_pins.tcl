# Requires an open synthesized design so the selected part's package is known.
proc ramp_validate_reference_pins {input_type {requested_pins {}}} {
 set ports [expr {$input_type eq "differential" ? {sys_clk_p sys_clk_n} : {sys_clk}}]
 # Vivado can silently move P when assigning N. Validate original board values,
 # then compare the resolved design ports, never just their inferred pairing.
 if {[llength $requested_pins] && [llength $requested_pins]!=[llength $ports]} {error "Incorrect reference pin argument count"}
 if {$input_type eq "differential" && ![llength $requested_pins]} {error "Original P/N pins are required for differential validation"}
 set pins {}
 set resolved {}
 foreach port_name $ports {
  set port [get_ports -quiet $port_name]
  if {[llength $port]!=1} {error "Reference clock port missing: $port_name"}
  lappend resolved [get_property PACKAGE_PIN $port]
 }
 if {![llength $requested_pins]} {set requested_pins $resolved}
 foreach name $requested_pins {
  if {$name in {"" "-"}} {
   puts "Reference clock pins incomplete: package-pair check deferred until pins are assigned."
   return
  }
  set pin [get_package_pins -quiet $name]
  if {[llength $pin]!=1 || ![get_property IS_CLK_CAPABLE $pin]} {
   error "Reference clock pin $name must be a clock-capable package pin on the selected FPGA."
  }
  lappend pins $pin
 }
 if {$input_type eq "differential"} {
  lassign $pins p n
  if {![get_property IS_MASTER $p] || [get_property DIFF_PAIR_PIN $p] ne [get_property NAME $n]} {
   error "sys_clk_p / sys_clk_n must be the P / N members of ONE clock-capable differential pair. Check board schematic and FPGA package."
  }
 }
 foreach expected $requested_pins actual $resolved port $ports {
  if {$expected ne $actual} {error "Reference pin changed by Vivado: $port requested=$expected actual=$actual. Check original P/N assignments."}
 }
 puts "Reference clock package pins verified: $pins ($input_type). Electrical and board timing checks remain required."
}
