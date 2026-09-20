import json
from pathlib import Path
import subprocess
import sys
import pytest
from host.flash import FlashSettings,job_config,tcl_word,bitstream_part
from host.board import Board,generate_xdc
from tkinter import Tcl,TclError


def test_flash_settings_persist_and_reach_generated_constraints(tmp_path):
    cfg=FlashSettings(cfgmem_part='mt25ql128-spi-x1_x2_x4',interface='SPIx4',config_rate=33,falling_edge=False)
    board=Board(flash=cfg.to_dict());loaded=Board.from_dict(json.loads(json.dumps(board.to_dict())))
    xdc=generate_xdc(loaded)
    assert 'CONFIG_MODE SPIx4' in xdc and 'SPI_BUSWIDTH 4' in xdc and 'CONFIGRATE 33' in xdc and 'SPI_FALL_EDGE No' in xdc
    assert Board.from_dict({'schema':2}).flash=={}
    with pytest.raises(ValueError):FlashSettings(size_mib=128000).validate()
    with pytest.raises(ValueError):FlashSettings().validate('program')
    from tkinter import Tcl
    t=Tcl();text='path $env(HOME) [error injected] {braces} \\ "quotes"'
    t.eval('set value '+tcl_word(text));assert t.getvar('value')==text
    bit=tmp_path/'test.bit';bit.write_bytes(bytes.fromhex('00090ff00ff00ff00ff0000001')+b'a\x00\x02x\x00b\x00\x0c7a35tfgg484\x00')
    assert bitstream_part(bit)=='7a35tfgg484'
    bit.write_bytes(b'not a bitstream')
    with pytest.raises(ValueError):bitstream_part(bit)


def run_flash_tcl_scenario(tmp_path,scenario,operation,expected,source_kind):
    from tkinter import Tcl,TclError
    bit=tmp_path/'input.bit';bit.write_bytes(b'fake input')
    cfg=FlashSettings(cfgmem_part='flash',size_mib=1,target='target',device='device',bitfile=str(bit))
    metadata='set cfg(bit_part) 7a35tfgg484\n'
    if source_kind!='bit':
        image=tmp_path/('input.'+source_kind);image.write_bytes(b'image')
        verify=tmp_path/'firmware.bin';verify.write_bytes(b'image')
        cfg.image_source='image';cfg.image_file=str(image)
        metadata+='set cfg(image_idcode) 0362D093\nset cfg(program_file) '+tcl_word(image.as_posix())+'\nset cfg(verify_file) '+tcl_word(verify.as_posix())+'\n'
    config=tmp_path/'config.tcl';config.write_text(job_config(cfg,operation,tmp_path)+metadata,encoding='utf-8')
    t=Tcl();t.setvar('scenario',scenario);t.setvar('argc',1);t.setvar('argv',t.call('list',str(config)));t.eval(r"""
set events {}
set generated 0
set bin_offset unset
set program_path unset
if {![llength [info commands real_puts]]} {rename puts real_puts}
proc puts {args} {if {[llength $args]==1 || [lindex $args 0] in {stderr stdout}} {return};real_puts {*}$args}
proc exit {n} {set ::exitcode $n;error "TEST_EXIT"}
proc get_hw_targets {args} {return target}
proc get_hw_devices {args} {return device}
proc get_cfgmem_parts {args} {return flash}
proc get_parts {args} {return part}
foreach cmd {open_hw_manager connect_hw_server open_hw_target close_hw_target disconnect_hw_server close_hw_manager current_hw_device refresh_hw_device create_hw_cfgmem create_hw_bitstream boot_hw_device} {
 proc $cmd {args} {}
}
proc get_property {name obj} {
 switch -- $name {
  PART {return [expr {$::scenario eq "part" ? "xc7a100t" : "xc7a35t"}]}
  IDCODE {if {$::scenario eq "part"} {return 0x03631093};return 0x1362d093}
  MEM_DENSITY {return [expr {$::scenario eq "density" ? 16 : 8}]}
  DATA_WIDTH {return x1_x2_x4}
  REGISTER.USR_ACCESS {return 09112040}
  REGISTER.CONFIG_STATUS.BIT14_DONE_PIN {return 1}
  REGISTER.CONFIG_STATUS.BIT00_CRC_ERROR {return 0}
  default {return helper}
 }
}
proc set_property {name value obj} {
 if {$name eq "PROGRAM.ERASE" && $value==1} {lappend ::events erase}
 if {$name eq "PROGRAM.FILES"} {set ::program_path [lindex $value 0]}
 if {$name eq "PROGRAM.BIN_OFFSET"} {set ::bin_offset $value}
}
proc program_hw_devices {args} {lappend ::events helper}
proc program_hw_cfgmem {args} {lappend ::events program}
proc write_cfgmem {args} {incr ::generated;set f [open [lindex $args end] wb];puts -nonewline $f image;close $f}
proc readback_hw_cfgmem {args} {
 set path [lindex $args [expr {[lsearch -exact $args -file]+1}]]
 set f [open $path wb]
 if {[lsearch -exact $args -all]>=0} {
  lappend ::events backup
  chan truncate $f [expr {$::scenario eq "backup" ? 10 : 1048576}]
 } else {
  lappend ::events compare
  puts -nonewline $f [expr {$::scenario eq "compare" ? "bad" : "image"}]
 }
 close $f
}
""")
    with pytest.raises(TclError,match='TEST_EXIT'):t.call('source',str(Path('scripts/vivado/flash_studio.tcl').resolve()))
    assert int(t.getvar('exitcode'))==expected, str(t.getvar('message'))
    events=tuple(map(str,t.splitlist(t.getvar('events'))))
    assert int(t.getvar('generated'))==(2 if source_kind=='bit' and operation=='program' else 0)
    if scenario in ('density','part') or operation=='status':assert events==()
    elif scenario=='backup':assert events==('helper','backup')
    else:assert events==('helper','backup','erase','program','compare')
    if 'program' in events:
        assert str(t.getvar('program_path'))==(image.as_posix() if source_kind!='bit' else (tmp_path/'firmware.mcs').as_posix())
        assert str(t.getvar('bin_offset'))==('0' if source_kind=='bin' else 'unset')


@pytest.mark.parametrize('scenario,operation,expected',[('ok','program',0),('density','program',1),('backup','program',1),('compare','program',1),('part','program',1),('ok','status',0)])
@pytest.mark.parametrize('source_kind',['bit','mcs','bin'])
def test_flash_tcl_backup_preflight_verify_and_read_only(tmp_path,scenario,operation,expected,source_kind):
    # Tcl on Windows caches native file handles across pytest's fd capture
    # changes. A child with stable stdio tests the actual script reliably;
    # all hardware commands are mocked inside run_flash_tcl_scenario.
    code='from pathlib import Path; import sys; from tests.test_flash import run_flash_tcl_scenario; run_flash_tcl_scenario(Path(sys.argv[1]),sys.argv[2],sys.argv[3],int(sys.argv[4]),sys.argv[5])'
    result=subprocess.run([sys.executable,'-X','utf8','-c',code,str(tmp_path),scenario,operation,str(expected),source_kind],
                          capture_output=True,text=True,encoding='utf-8',timeout=30,
                          creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    assert result.returncode==0,result.stdout+result.stderr


def test_flash_gui_settings_persist_and_serial_connection_blocks_jtag(tmp_path):
    import os
    os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
    from PySide6.QtWidgets import QApplication
    from host.app import Studio
    from host.storage import Library
    from tests.test_gui import until
    app=QApplication.instance() or QApplication([]);w=Studio(Library(tmp_path));w.show();page=w.board_page.flash_page
    try:
        page.preset();page.interface.setCurrentText('SPIx4');page.rate.setCurrentIndex(page.rate.findData(33));w.board_page.save()
        assert json.loads((tmp_path/'board.json').read_text(encoding='utf-8'))['flash']['interface']=='SPIx4'
        w.connect_button.click();until(app,lambda:w.connected and not w.busy)
        assert not page.buttons['program'].isEnabled() and not page.buttons['temporary'].isEnabled() and not page.buttons['boot'].isEnabled()
        assert page.buttons['generate'].isEnabled()
        w.send('disconnect',None);until(app,lambda:not w.connected and not w.busy)
        assert page.buttons['program'].isEnabled()
    finally:w.close();app.processEvents()
    restored=Studio(Library(tmp_path))
    try:assert restored.board_page.flash_page.settings().interface=='SPIx4' and restored.board_page.flash_page.settings().config_rate==33
    finally:restored.close();app.processEvents()


def test_flash_existing_image_gui_session_and_offline_worker(tmp_path):
    import os
    os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
    from PySide6.QtWidgets import QApplication
    from host.app import Studio
    from host.storage import Library
    from tests.test_gui import until
    from tests.test_flash_image import boot_bytes
    app=QApplication.instance() or QApplication([]);w=Studio(Library(tmp_path));w.show()
    page=w.board_page.flash_page
    image=tmp_path/'existing image.bin';image.write_bytes(boot_bytes())
    try:
        page.bit.setText('D:/remember original.bit')
        page.source.setCurrentIndex(page.source.findData('image'));page.image.setText(str(image))
        assert page.boot_section.isHidden() and page.bit_row.isHidden() and not page.image_row.isHidden()
        assert not page.buttons['generate'].isEnabled() and not page.buttons['temporary'].isEnabled()
        assert page.buttons['inspect'].isEnabled() and page.buttons['program'].isEnabled()
        assert not page.rate.isEnabled() and not page.falling.isEnabled()
        assert w.board_page.save()
        saved=json.loads((tmp_path/'board.json').read_text(encoding='utf-8'))['flash']
        assert saved['image_source']=='image' and saved['image_file']==str(image)
        w.board_page.vivado.setText('missing Vivado; offline check must still work')
        page.start('inspect');until(app,lambda:not page.running(),timeout=30)
        result=json.loads((page.job_dir/'result.json').read_text(encoding='utf-8'))
        assert result['passed'] and result['image']['idcode']=='0362D093'
        assert not (page.job_dir/'config.tcl').exists()
        assert '未连接硬件' in page.feedback.text()
    finally:w.close();app.processEvents()
    restored=Studio(Library(tmp_path));page=restored.board_page.flash_page
    try:
        assert page.source.currentData()=='image' and page.image.text()==str(image)
        assert page.boot_section.isHidden() and not page.running()
        page.source.setCurrentIndex(page.source.findData('bit'))
        assert page.bit.text()=='D:/remember original.bit' and page.buttons['generate'].isEnabled()
    finally:restored.close();app.processEvents()
