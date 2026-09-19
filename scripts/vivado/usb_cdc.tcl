# Gray-pointer and bundled FIFO payload constraints. Never mark USB/core clock domains asynchronous:
# doing so would override these max delays and hide unprotected payload paths.
# FIFO hierarchy is preserved by KEEP_HIERARCHY in usb_async_fifo.v. This file
# runs after open_run synth_1 / before implementation optimization, when IP
# checkpoints, board USB and generated core clocks are linked. Do not register this as an XDC:
# Vivado's XDC reader rejects the foreach/if used to validate these endpoints.
set usb_cdc_fifo_count 0
set usb_cdc_crossing_count 0
# Only asynchronous assertion pins of the three-stage reset synchronizers are
# exempt. Their shift data paths and every downstream functional reset remain
# timed. Reset release at the consumer is synchronous to its own clock.
set usb_reset_sync [get_cells -quiet -hier -filter {ASYNC_REG == TRUE && (NAME =~ *usb_reset_reg* || NAME =~ *usb_reset_pipe_reg* || NAME =~ *core_reset_pipe_reg*)}]
set usb_reset_assert [get_pins -quiet -of_objects $usb_reset_sync -filter {REF_PIN_NAME == PRE || REF_PIN_NAME == CLR}]
if {[llength $usb_reset_assert]} {set_false_path -to $usb_reset_assert}
foreach fifo [get_cells -quiet -hier -filter {REF_NAME =~ usb_async_fifo* || ORIG_REF_NAME =~ usb_async_fifo*}] {
    incr usb_cdc_fifo_count
    foreach direction {wr rd} {
        set destination [get_cells -quiet -hier -filter "NAME =~ ${fifo}/${direction}_gray_sync1_reg*"]
        set destination_d [get_pins -quiet -of_objects $destination -filter {REF_PIN_NAME == D}]
        # Gray MSB equals binary MSB; synthesis may merge those registers. Trace
        # the real first synchronizer D drivers rather than relying on a Gray
        # source register name that is not guaranteed to survive optimization.
        set source_start [all_fanin -flat -startpoints_only -to $destination_d]
        set source [get_cells -quiet -of_objects $source_start]
        set source_q [get_pins -quiet -of_objects $source -filter {REF_PIN_NAME == Q}]
        set source_clocks [get_clocks -quiet -of_objects [get_pins -quiet -of_objects $source -filter {REF_PIN_NAME == C}]]
        if {![llength $source_q] || [llength $source_q] != [llength $destination_d] || ![llength $source_clocks]} {
            error "USB FIFO CDC endpoints/clocks missing: $fifo / $direction (Q=[llength $source_q], D=[llength $destination_d], clocks=[llength $source_clocks])"
        }
        set period 1000000.0
        foreach source_clock $source_clocks {
            set candidate [get_property PERIOD $source_clock]
            if {$candidate > 0 && $candidate < $period} {set period $candidate}
        }
        if {$period == 1000000.0} {error "USB FIFO CDC source clock has no valid period: $fifo / $direction"}
        # The timing startpoint is C, not Q. Starting at Q would segment the
        # path and suppress clock-to-Q and CDC analysis (Constraints 18-515).
        set_max_delay -datapath_only $period -from $source_start -to $destination_d
        set_bus_skew $period -from $source_start -to $destination_d
        incr usb_cdc_crossing_count
    }
    # FIFO payload is bundled data: writes become readable only after the
    # Gray write pointer traverses two read-clock synchronizer stages. Bound
    # memory output -> consumer data/control to one read period, so it settles
    # before that valid indication can permit a read. This also covers the
    # FF implementation if a tool cannot infer distributed dual-port RAM.
    # Do not exempt read-address logic or whole clock domains from timing.
    set read_data [get_pins -quiet -of_objects $fifo -filter {REF_PIN_NAME =~ rd_data*}]
    set memory_start {}
    foreach startpoint [all_fanin -flat -startpoints_only -to $read_data] {
        if {[string match "${fifo}/memory_reg*" $startpoint]} {lappend memory_start $startpoint}
    }
    set consumers [all_fanout -flat -endpoints_only -from $read_data]
    set read_counter [get_cells -quiet -hier -filter "NAME =~ ${fifo}/rd_binary_reg*"]
    set read_clocks [get_clocks -quiet -of_objects [get_pins -quiet -of_objects $read_counter -filter {REF_PIN_NAME == C}]]
    if {![llength $memory_start] || ![llength $consumers] || [llength $read_clocks] != 1} {
        error "USB FIFO payload endpoints/read clock missing: $fifo (memory=[llength $memory_start], consumers=[llength $consumers], clocks=[llength $read_clocks])"
    }
    set read_period [get_property PERIOD $read_clocks]
    if {$read_period <= 0} {error "USB FIFO payload read clock has no valid period: $fifo"}
    set_max_delay -datapath_only $read_period -from $memory_start -to $consumers
    puts "RAMP_USB_PAYLOAD_CDC: $fifo, [llength $memory_start] memory startpoints, [llength $consumers] consumers, ${read_period}ns"
}
if {$usb_cdc_fifo_count==0} {error "USB FIFO CDC: no FIFO instances found; expected preserved usb_async_fifo hierarchy"}
puts "RAMP_USB_CDC_APPLIED: $usb_cdc_fifo_count FIFOs, $usb_cdc_crossing_count Gray crossings"
