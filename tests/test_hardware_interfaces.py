from dataclasses import replace
import json
import os
import pytest
from host.board import Board,ports_for_board,generate_xdc,validate
from host.hardware_interfaces import UsbFifoSettings,USB_PORTS,DAVINCI_USB_PINS,apply_davinci_interfaces


def test_old_board_keeps_uart_bram_without_optional_usb():
    b=Board.from_dict({'schema':1,'pins':{'sys_clk':'R4'}})
    assert not UsbFifoSettings.from_dict(b.usb_fifo).enabled
    assert not set(ports_for_board(b)) & set(USB_PORTS)
    assert 'usb_clk' not in generate_xdc(b)


def test_usb_template_preserves_uart_dac_trigger_and_waveforms():
    b=Board(dac_count=4);before=b.to_dict();result=apply_davinci_interfaces(b)
    assert len(USB_PORTS)==15 and set(USB_PORTS)==set(DAVINCI_USB_PINS)
    assert b.to_dict()==before
    assert all(result.pins[p]==pin for p,pin in b.pins.items())
    assert result.pins['usb_clk']=='Y4' and result.pins['usb_data[0]']=='AB5'
    assert 'uart_rx' in ports_for_board(result) and 'uart_tx' in ports_for_board(result)
    assert not validate(result)
    assert Board.from_dict(json.loads(json.dumps(result.to_dict())))==result


@pytest.mark.parametrize('changes',[{'pins':{'sys_clk':'Y4'}},{'pins':{'trigger_in[0]':'AB5'}},
    {'pins':{'usb_clk':'A5'}},{'part':'xc7a100tfgg484-2'}])
def test_template_rejects_conflicts_atomically(changes):
    b=Board(**changes);before=b.to_dict()
    with pytest.raises(ValueError):apply_davinci_interfaces(b)
    assert b.to_dict()==before


def test_usb_xdc_separates_standard_and_keeps_both_delay_bounds():
    text=generate_xdc(apply_davinci_interfaces(Board(io_standard='LVCMOS18')))
    assert 'PACKAGE_PIN Y4 IOSTANDARD LVCMOS33' in text
    assert 'create_clock -name usb_clk -period 16.666667' in text
    for cmd in ('set_input_delay','set_output_delay'):
        assert cmd+' -clock [get_clocks usb_clk] -min ' in text
        assert cmd+' -clock [get_clocks usb_clk] -max ' in text
    assert '-max 9.000000 [get_ports {usb_data[*] usb_rxf_n usb_txe_n}]' in text
    assert '-max 7.500000 [get_ports {usb_data[*] usb_rd_n usb_wr_n usb_oe_n}]' in text
    assert 'set_property SLEW FAST [get_ports {usb_data[*] usb_rd_n usb_wr_n usb_oe_n}]' in text
    assert 'ddr3' not in text.lower() and 'MIG_PIN' not in text


@pytest.mark.parametrize('settings',[{'enabled':'true'},{'enabled':True,'clock_mhz':30},
    {'enabled':True,'input_delay_max':float('nan')},{'enabled':True,'output_delay_min':8,'output_delay_max':7.5}])
def test_usb_invalid_settings_rejected(settings):
    assert validate(Board(usb_fifo=settings))


def test_removed_external_memory_never_masquerades_as_bram():
    with pytest.raises(ValueError,match='DDR3'):
        Board.from_dict({'schema':3,'ddr3':{'enabled':True,'runs_per_bank':32768}})
    assert 'ddr3' not in Board.from_dict({'schema':3,'ddr3':{'enabled':False}}).to_dict()


def test_usb_gui_settings_and_portable_roundtrip(tmp_path):
    os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
    from PySide6.QtWidgets import QApplication
    from host.board_page import BoardPage
    from host.storage import Library
    app=QApplication.instance() or QApplication([])
    page=BoardPage(Library(tmp_path));other=BoardPage(Library(tmp_path/'other'))
    try:
        page.apply_interface_template()
        assert page.usb_enabled.isChecked() and page.uart_baud.isEnabled()
        assert page.port_filter.findData('usb')>=0
        assert page.port_filter.findData('ddr3')<0 and not hasattr(page,'ddr_enabled')
        page.usb_input_max.setText('9.2');page.usb_enabled.setChecked(False)
        snapshot=json.loads(json.dumps(page.capture_session()))
        assert other.restore_session(snapshot) and other.capture_session()==snapshot
        assert other.collect().pins['usb_clk']=='Y4'
        assert other.collect().usb_fifo['input_delay_max']==9.2
        other.usb_enabled.setChecked(True)
        assert any(other.table.item(i,0).text()=='usb_data[7]' for i in range(other.table.rowCount()))
        assert other.save()
        stored=Board.from_dict(json.loads((tmp_path/'other/board.json').read_text(encoding='utf-8')))
        assert stored.usb_fifo['enabled'] and 'ddr3' not in stored.to_dict()
        assert not (tmp_path/'board.json').exists()
    finally:
        page.close();other.close();app.processEvents()
