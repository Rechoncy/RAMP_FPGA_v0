"""Run the real Vivado entry script against a Tcl command recorder."""
from pathlib import Path
from functools import lru_cache

import pytest

ROOT = Path(__file__).resolve().parents[1]


@lru_cache(maxsize=1)
def tcl_interpreter():
    # Reuse one Tcl runtime: repeatedly destroying Tcl's standard IO channels
    # can close the file descriptors owned by pytest capture on Windows.
    tkinter = pytest.importorskip('tkinter')
    return tkinter.Tcl()


def run_script(tmp_path, argc=16, baud='1000000', kind='single_ended', interfaces=False, boot=False, core_mhz=None):
    tkinter = pytest.importorskip('tkinter')
    interp = tcl_interpreter()
    args = ['xc7a35tfgg484-2', '50', str(tmp_path / 'board.xdc'), str(tmp_path / 'output'),
            'generate', '4', '2', '10', 'SPIx4', '16', '1', '-', kind, 'R4', '-', baud]
    if boot:
        boot_dir=tmp_path/'boot';boot_dir.mkdir()
        (boot_dir/'ramp_boot_top.v').write_text('// fixture',encoding='ascii')
        (boot_dir/'boot_dac0.mem').write_text('0000',encoding='ascii')
        args[11]=str(boot_dir)
    if interfaces:
        args.append(str(tmp_path/'interfaces'));argc=17
    if core_mhz is not None:
        if not interfaces:args.append('-')
        args.append(str(core_mhz));argc=18
    interp.setvar('argc', argc)
    interp.setvar('argv', tuple(args[:argc]))
    interp.eval('''
        set recorded {}
        set created 0
        proc puts {args} {}
        proc exit {code} {error "script_exit:$code"}
        proc create_project {args} {set ::created 1}
        proc set_property {args} {lappend ::recorded $args}
        proc current_project {} {return project}
        proc current_fileset {} {return sources_1}
        proc get_runs {args} {return impl_1}
        proc get_ips {args} {return clk_core_ip}
        proc get_files {args} {return memory_file}
        proc add_files {args} {}
        proc create_ip {args} {}
        proc generate_target {args} {}
        proc update_compile_order {args} {}
        proc close_project {} {}
    ''')
    try:
        interp.call('source', str(ROOT / 'scripts/vivado/build_studio.tcl'))
    except tkinter.TclError as error:
        status = str(error)
    else:
        raise AssertionError('Build script did not exit')
    properties = dict((item[0], item[1]) for item in interp.getvar('recorded'))
    return status, int(interp.getvar('created')), properties


@pytest.mark.parametrize('argc', [5, 7, 8, 11, 12, 13, 15])
def test_old_build_argument_layouts_default_to_115200(tmp_path, argc):
    status, created, properties = run_script(tmp_path, argc=argc)
    assert status == 'script_exit:0' and created == 1
    assert 'UART_BAUD=115200' in properties['generic']
    assert properties['top'] == 'ramp_top'


@pytest.mark.parametrize('kind', ['single_ended', 'differential'])
@pytest.mark.parametrize('baud', [9600, 19200, 38400, 57600, 115200, 230400, 460800, 921600, 1000000])
def test_build_passes_baud_for_both_clock_topologies(tmp_path, kind, baud):
    status, created, properties = run_script(tmp_path, baud=str(baud), kind=kind)
    assert status == 'script_exit:0' and created == 1
    assert f'UART_BAUD={baud}' in properties['generic']
    assert properties['top'] == ('ramp_top_diff' if kind == 'differential' else 'ramp_top')


@pytest.mark.parametrize('baud', ['0', '-1', '1000001', '2000000', '1000000.0', 'bad'])
def test_build_rejects_invalid_baud_before_creating_project(tmp_path, baud):
    status, created, _ = run_script(tmp_path, baud=baud)
    assert status == 'script_exit:1' and created == 0


@pytest.mark.parametrize('boot',[False,True])
def test_usb_build_uses_linked_cdc_hooks_and_preserves_boot_top(tmp_path,boot):
    status,created,properties=run_script(tmp_path,interfaces=True,boot=boot)
    assert status=='script_exit:0' and created==1
    assert properties['top']==('ramp_boot_top' if boot else 'ramp_extended_top')
    expected=(ROOT/'scripts/vivado/usb_cdc.tcl').as_posix()
    assert 'STEPS.SYNTH_DESIGN.TCL.POST' not in properties
    assert str(properties['STEPS.OPT_DESIGN.TCL.PRE'])==expected


@pytest.mark.parametrize('core_mhz',[100,125,200])
@pytest.mark.parametrize('interfaces',[False,True])
def test_selected_core_frequency_and_optional_interface_arguments(tmp_path,core_mhz,interfaces):
    status,created,props=run_script(tmp_path,core_mhz=core_mhz,interfaces=interfaces)
    assert status=='script_exit:0' and created==1
    assert str(core_mhz) in str(props['-dict'])
    if not interfaces:assert f'CORE_HZ={core_mhz*1000000}' in props['generic']


def test_unsupported_output_frequency_rejected(tmp_path):
    status,created,_=run_script(tmp_path,core_mhz=150)
    assert 'Output rate' in status and created==0
