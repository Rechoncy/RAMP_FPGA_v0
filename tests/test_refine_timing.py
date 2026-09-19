from pathlib import Path

import pytest

from test_uart_build_tcl import tcl_interpreter


def run_refinement(setup):
    interp=tcl_interpreter()
    interp.eval('''
        set optimization_count 0
        set routing_count 0
        proc get_timing_paths {args} {return [lindex $args 1]}
        proc get_property {property path} {
            if {$path eq "max"} {return $::setup_slack}
            return 0.020
        }
        proc phys_opt_design {args} {incr ::optimization_count;set ::setup_slack 0.050}
        proc route_design {args} {incr ::routing_count}
        proc puts {args} {}
    ''')
    interp.setvar('setup_slack',setup)
    interp.call('source',str(Path(__file__).resolve().parents[1]/'scripts/vivado/refine_timing.tcl'))
    interp.call('ramp_refine_timing')
    return float(interp.getvar('setup_slack')),int(interp.getvar('optimization_count')),int(interp.getvar('routing_count'))


def test_large_setup_failure_stays_failed_without_futile_refinement():
    assert run_refinement(-0.75)==(-0.75,0,0)


def test_small_setup_failure_still_gets_physical_timing_refinement():
    assert run_refinement(-0.2)==(0.05,1,1)
