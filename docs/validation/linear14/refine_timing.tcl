# Refine placement/routing only when the constrained design still fails timing.
# Never relax clocks, remove constraints, or waive a failing path.
proc ramp_refine_timing {} {
 for {set attempt 0} {$attempt<2} {incr attempt} {
  set failing 0
  foreach delay {max min} {
   set path [get_timing_paths -delay_type $delay -max_paths 1]
   if {[llength $path]==0} {error "No constrained $delay timing path"}
   if {[get_property SLACK $path]<0} {set failing 1}
  }
  if {!$failing} {return}
  puts "RAMP_TIMING_REFINEMENT: pass [expr {$attempt+1}]"
  phys_opt_design -directive AggressiveExplore
  route_design -directive Explore
 }
}
