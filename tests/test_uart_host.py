"""Baud selection reaches the serial driver and full replies never wait for 64 bytes."""
import os
import json
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
import pytest
from PySide6.QtWidgets import QApplication,QMessageBox
from PySide6.QtCore import QSettings
from host.protocol import SerialTransport,Device,encode,INFO,STATUS
from host.simulator import Simulator
from host.uart_settings import BAUD_RATES
from host.worker import DeviceWorker
from host.app import Studio
from host.storage import Library
from tests.test_gui import until


class FakeSerial:
    """Immediate/fragmented serial bytes from a simulated FPGA, with strict reads."""
    opened=[]
    def __init__(self,**settings):
        self.settings=settings;self.buffer=bytearray();self.reads=[];self.writes=[];self.closed=False
        self.sim=Simulator();self.opened.append(self)
    def send_break(self,duration):self.break_duration=duration
    def flush(self):pass
    def reset_input_buffer(self):self.buffer.clear()
    @property
    def in_waiting(self):return min(7,len(self.buffer))
    def write(self,packet):
        self.writes.append(packet)
        if packet==b'\x55'*16:return len(packet)
        reply=self.sim.exchange(packet)
        self.buffer.extend(reply);return len(packet)
    def read(self,size):
        self.reads.append(size)
        assert size<=len(self.buffer), 'Would block unnecessarily for an incomplete read'
        data=bytes(self.buffer[:size]);del self.buffer[:size];return data
    def close(self):self.closed=True;self.sim.close()


@pytest.fixture
def fake_serial(monkeypatch):
    import serial
    FakeSerial.opened=[];monkeypatch.setattr(serial,'Serial',FakeSerial)
    return FakeSerial


@pytest.mark.parametrize('baudrate',BAUD_RATES)
def test_all_baud_presets_use_8n1_and_parse_fragmented_response(fake_serial,baudrate):
    transport=SerialTransport('COM-test',baudrate)
    try:
        settings=transport.serial.settings
        assert settings['baudrate']==baudrate and settings['port']=='COM-test'
        assert (settings['bytesize'],settings['parity'],settings['stopbits'])==(8,'N',1)
        assert not settings['xonxoff'] and not settings['rtscts'] and not settings['dsrdtr']
        device=Device(transport);assert device.caps.core_hz==200_000_000
        assert device.status()['locked'] and max(transport.serial.reads)<=7
    finally:transport.close()
    assert transport.serial.closed


@pytest.mark.parametrize('invalid',[True,'1000000',1_000_000.0,0,1_000_001,2_000_000])
def test_unsupported_baud_is_rejected_before_open(fake_serial,invalid):
    with pytest.raises(ValueError,match='波特率'):SerialTransport('COM-test',invalid)
    assert not fake_serial.opened


def test_training_occurs_once_per_connection_before_info(fake_serial):
    transport=SerialTransport('COM-test',1_000_000)
    try:
        device=Device(transport)
        assert transport.serial.break_duration>=0.02
        assert transport.serial.writes[0]==b'\x55'*16
        assert transport.serial.writes[1][3]==INFO
        for _ in range(3):device.refresh_capabilities();device.status()
        assert transport.serial.writes.count(b'\x55'*16)==1
        assert not device.caps.uart_autobaud # previous INFO reply remains compatible
    finally:transport.close()


@pytest.mark.parametrize('baudrate',BAUD_RATES)
def test_training_waits_for_usb_uart_fifo_and_then_idle(fake_serial,monkeypatch,baudrate):
    sleeps=[];monkeypatch.setattr('host.protocol.time.sleep',sleeps.append)
    transport=SerialTransport('COM-test',baudrate)
    try:
        transport.synchronize()
        assert len(sleeps)==1
        assert sleeps[0]>=16*12/baudrate + max(.01,24/baudrate)
    finally:transport.close()


def test_short_training_write_fails_and_worker_closes_port(fake_serial,monkeypatch):
    app=QApplication.instance() or QApplication([])
    original=fake_serial.write
    monkeypatch.setattr(fake_serial,'write',lambda self,data:3 if data==b'\x55'*16 else original(self,data))
    worker=DeviceWorker();errors=[];worker.failed.connect(lambda *args:errors.append(args))
    worker.execute('connect',dict(port='COM-test',baudrate=1_000_000))
    assert errors and worker.device is None and fake_serial.opened[-1].closed
    app.processEvents()


def test_capability_reports_automatic_uart_without_changing_other_flags():
    sim=Simulator();original=sim.exchange
    def reply(frame):
        raw=original(frame)
        if frame[3]==INFO:
            payload=bytearray(raw[6:30]);payload[19]|=16
            return encode(raw[3],int.from_bytes(raw[4:6],'little'),payload)
        return raw
    sim.exchange=reply
    try:
        caps=Device(sim).caps
        assert caps.uart_autobaud and caps.core_hz==200_000_000
    finally:sim.close()


def test_old_call_defaults_and_worker_routes_real_dict_without_demo(fake_serial):
    app=QApplication.instance() or QApplication([])
    transport=SerialTransport('COM-legacy');assert transport.serial.settings['baudrate']==115200;transport.close()
    worker=DeviceWorker();errors=[];results=[]
    worker.failed.connect(lambda *args:errors.append(args));worker.result.connect(lambda *args:results.append(args))
    try:
        worker.execute('connect',dict(port='COM-test',baudrate=1_000_000))
        assert not errors and results[-1][0]=='connect'
        assert worker.device.transport.serial.settings['baudrate']==1_000_000
        worker.execute('disconnect',None);assert fake_serial.opened[-1].closed
        worker.execute('connect',dict(port='demo',dac_count=4,trigger_count=2,dac_bits=14))
        assert worker.device.caps.dac_count==4 and len(fake_serial.opened)==2 and not errors
    finally:worker.disconnect()
    app.processEvents()


def test_stale_sequence_noise_and_bad_crc_do_not_replace_requested_reply(fake_serial):
    transport=SerialTransport('COM-test',1_000_000)
    try:
        old=transport.serial.write
        def noisy(packet):
            corrupt=bytearray(encode(INFO|0x80,42,b'bad'));corrupt[-1]^=1
            transport.serial.buffer.extend(b'noise'+bytes(corrupt)+encode(INFO|0x80,41,b'stale'))
            return old(packet)
        transport.serial.write=noisy
        device=Device(transport);assert device.caps.core_hz==200_000_000
        assert len([p for p in transport.serial.writes if len(p)==32])==1
        assert transport.serial.writes[0]==b'\x55'*16
    finally:transport.close()


def test_connection_setting_is_saved_locked_while_connected_and_reopens_offline(tmp_path,monkeypatch,fake_serial):
    app=QApplication.instance() or QApplication([]);app.setStyle('Fusion')
    QSettings.setDefaultFormat(QSettings.IniFormat);QSettings.setPath(QSettings.IniFormat,QSettings.UserScope,str(tmp_path/'preferences'))
    errors=[];monkeypatch.setattr(QMessageBox,'warning',lambda *args:errors.append(args[-1]))
    w=Studio(Library(tmp_path/'data'));w.show();w.navigate(1)
    try:
        assert w.baudrate.currentData()==115200 and w.baudrate.count()==len(BAUD_RATES)
        w.ports.addItem('COM-test','COM-test');w.ports.setCurrentIndex(w.ports.findData('COM-test'))
        w.baudrate.setCurrentIndex(w.baudrate.findData(1_000_000))
        assert w.board_page.uart_baud.currentData()==115200 # editing the connection never rewrites firmware settings
        w.connect_button.click();until(app,lambda:w.connected and not w.busy)
        assert not w.demo and not w.baudrate.isEnabled() and '1,000,000 baud' in w.hardware_summary.text()
        assert fake_serial.opened[-1].settings['baudrate']==1_000_000
        w.connect_button.click();until(app,lambda:not w.connected and not w.busy)
        assert w.baudrate.isEnabled()
    finally:
        if w.connected:w.send('disconnect',None);until(app,lambda:not w.connected and not w.busy)
        w.close();until(app,lambda:not w.thread.isRunning(),timeout=20)
    assert json.loads((tmp_path/'data/desktop_session.json').read_text('utf-8'))['view']['baudrate']==1_000_000
    before=len(fake_serial.opened);other=Studio(Library(tmp_path/'data'));other.show()
    try:
        assert other.baudrate.currentData()==1_000_000 and other.ports.currentData()=='COM-test'
        assert not other.connected and len(fake_serial.opened)==before and not errors
        assert other.board_page.uart_baud.currentData()==115200
    finally:other.close();until(app,lambda:not other.thread.isRunning(),timeout=20)
