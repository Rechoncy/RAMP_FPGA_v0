# Refine placement/routing only when the constrained design still fails timing.
# Never relax clocks, remove constraints, or waive a failing path.
proc ramp_refine_timing {} {
 for {set attempt 0} {$attempt<2} {incr attempt} {
  set failing 0
  foreach delay {max min} {
   set path [get_timing_paths -delay_type $delay -max_paths 1]
   if {[llength $path]==0} {error "No constrained $delay timing path"}
   # Vivado reports post-route physical optimization as ineffective for a
   # setup miss this large. Preserve the failing reports and fix RTL/IO instead
   # of repeatedly spending minutes on the same infeasible implementation.
   if {$delay eq "max" && [get_property SLACK $path]<-0.5} {
    puts "RAMP_TIMING_REFINEMENT_SKIPPED: setup slack [get_property SLACK $path] ns requires an RTL/IO fix; timing remains failed"
    return
   }
   if {[get_property SLACK $path]<0} {set failing 1}
  }
  if {!$failing} {return}
  puts "RAMP_TIMING_REFINEMENT: pass [expr {$attempt+1}]"
  phys_opt_design -directive AggressiveExplore
  route_design -directive Explore
 }
}
