"""FT232H synchronous 245 FIFO transport using the installed FTDI D2XX driver.

No EEPROM programming or driver installation is performed. The board's FT232H
EEPROM must already select asynchronous 245 FIFO before software selects 0x40.
Only FT_DEVICE_232H data endpoints are enumerated. Dedicated Digilent/JTAG
adapters are excluded even when they also use an FT232H.
The firmware uses acknowledged 32-byte packets with a bounded upload window;
physical USB line rate is not a promise of application throughput.
"""
from __future__ import annotations

import ctypes
import math
import os
import time

from .protocol import FrameParser

FT_DEVICE_232H = 8
UPLOAD_WINDOW = 16  # 512 request bytes; no unbounded outstanding command queue.


class D2XXError(OSError):
    pass


class D2XXBackend:
    """Thin, injectable D2XX binding. Windows driver DLL stays system-managed."""

    def __init__(self, dll=None):
        if dll is None:
            if os.name != 'nt':
                raise D2XXError('高速 USB 当前使用 Windows FTDI D2XX 驱动')
            try:
                # Never load a same-named DLL from an untrusted working directory.
                dll = ctypes.WinDLL('ftd2xx.dll', winmode=0x00000800)
            except OSError as exc:
                raise D2XXError('无法加载 FTDI D2XX 驱动。请安装与本程序位数一致的官方 D2XX 驱动；CH340 串口驱动不能替代它') from exc
        self.dll = dll
        signatures = {
            'FT_CreateDeviceInfoList': [ctypes.POINTER(ctypes.c_uint32)],
            'FT_GetDeviceInfoDetail': [ctypes.c_uint32, ctypes.POINTER(ctypes.c_uint32),
                ctypes.POINTER(ctypes.c_uint32), ctypes.POINTER(ctypes.c_uint32),
                ctypes.POINTER(ctypes.c_uint32), ctypes.c_void_p, ctypes.c_void_p,
                ctypes.POINTER(ctypes.c_void_p)],
            'FT_OpenEx': [ctypes.c_void_p, ctypes.c_uint32, ctypes.POINTER(ctypes.c_void_p)],
            'FT_Close': [ctypes.c_void_p],
            'FT_SetBitMode': [ctypes.c_void_p, ctypes.c_ubyte, ctypes.c_ubyte],
            'FT_SetTimeouts': [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_uint32],
            'FT_SetUSBParameters': [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_uint32],
            'FT_SetLatencyTimer': [ctypes.c_void_p, ctypes.c_ubyte],
            'FT_SetFlowControl': [ctypes.c_void_p, ctypes.c_ushort, ctypes.c_ubyte, ctypes.c_ubyte],
            'FT_Purge': [ctypes.c_void_p, ctypes.c_uint32],
            'FT_GetQueueStatus': [ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint32)],
            'FT_Read': [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint32, ctypes.POINTER(ctypes.c_uint32)],
            'FT_Write': [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint32, ctypes.POINTER(ctypes.c_uint32)],
        }
        for name, args in signatures.items():
            function = getattr(self.dll, name)
            function.argtypes = args
            function.restype = ctypes.c_uint32

    def call(self, name, *args):
        status = getattr(self.dll, name)(*args)
        if status:
            raise D2XXError(f'FTDI {name} 失败（状态 {status}）；请检查 USB SLAVE 接口、D2XX 驱动和设备是否被其他程序占用')

    def enumerate_devices(self):
        count = ctypes.c_uint32()
        self.call('FT_CreateDeviceInfoList', ctypes.byref(count))
        devices = []
        for index in range(count.value):
            flags, kind, identity, location = (ctypes.c_uint32() for _ in range(4))
            serial, description = ctypes.create_string_buffer(16), ctypes.create_string_buffer(64)
            handle = ctypes.c_void_p()
            self.call('FT_GetDeviceInfoDetail', index, ctypes.byref(flags), ctypes.byref(kind),
                ctypes.byref(identity), ctypes.byref(location), serial, description, ctypes.byref(handle))
            description_text = description.value.decode('utf-8', errors='replace')
            # Digilent HS-class programmers may use the very same FT232H type
            # as the data FIFO. Never offer them for FT_SetBitMode(0x40).
            is_programmer = any(label in description_text.casefold() for label in ('digilent', 'jtag'))
            if kind.value == FT_DEVICE_232H and serial.value and not is_programmer:
                serial_text = serial.value.decode('ascii', errors='replace')
                devices.append(dict(port='usb:'+serial_text, serial=serial_text,
                    description=description_text,
                    type=kind.value, opened=bool(flags.value & 1)))
        return devices

    def open_device(self, serial):
        matches = [d for d in self.enumerate_devices() if d['serial'] == serial]
        if len(matches) != 1:
            raise D2XXError('未找到唯一的 FT232H USB 设备，请连接 USB SLAVE 接口并刷新列表')
        handle = ctypes.c_void_p()
        serial_buffer = ctypes.create_string_buffer(serial.encode('ascii'))
        self.call('FT_OpenEx', serial_buffer, 1, ctypes.byref(handle))
        return D2XXHandle(self, handle)


class D2XXHandle:
    def __init__(self, backend, handle):
        self.backend, self.handle = backend, handle
        self.closed = False

    def configure_fifo(self, read_timeout_ms=50, write_timeout_ms=1000):
        self.backend.call('FT_SetTimeouts', self.handle, read_timeout_ms, write_timeout_ms)
        self.backend.call('FT_SetUSBParameters', self.handle, 65536, 65536)
        self.backend.call('FT_SetLatencyTimer', self.handle, 2)
        self.backend.call('FT_SetFlowControl', self.handle, 0x0100, 0, 0)
        self.backend.call('FT_SetBitMode', self.handle, 0xff, 0)
        time.sleep(0.02)
        self.backend.call('FT_SetBitMode', self.handle, 0xff, 0x40)
        time.sleep(0.02)
        self.backend.call('FT_Purge', self.handle, 3)

    def write(self, data):
        buffer = ctypes.create_string_buffer(data)
        written = ctypes.c_uint32()
        self.backend.call('FT_Write', self.handle, buffer, len(data), ctypes.byref(written))
        return written.value

    def read(self, size):
        available = ctypes.c_uint32()
        self.backend.call('FT_GetQueueStatus', self.handle, ctypes.byref(available))
        # A one-byte wait when empty avoids spinning while preserving prompt replies.
        size = min(size, available.value or 1)
        buffer, received = ctypes.create_string_buffer(size), ctypes.c_uint32()
        self.backend.call('FT_Read', self.handle, buffer, size, ctypes.byref(received))
        return buffer.raw[:received.value]

    def close(self):
        if not self.closed:
            self.closed = True
            # Leave EEPROM and persistent configuration untouched. Closing a host
            # connection must not stop CLKOUT or reset a running FPGA DAC program.
            self.backend.call('FT_Close', self.handle)


def enumerate_usb_devices(backend=None):
    """Return selectable FT232H endpoints; missing drivers raise an actionable error."""
    return (backend or D2XXBackend()).enumerate_devices()


class USBTransport:
    upload_window=UPLOAD_WINDOW
    def __init__(self, port, *, backend=None, timeout=0.7, retries=3):
        if not isinstance(port, str) or not port.startswith('usb:') or not port[4:]:
            raise ValueError('高速 USB 设备标识应为 usb:序列号')
        if not isinstance(timeout,(int,float)) or not math.isfinite(timeout) or timeout <= 0 or type(retries) is not int or retries < 1:
            raise ValueError('USB 超时和重试次数必须为正值')
        self.port, self.timeout, self.retries = port, timeout, retries
        self.backend = backend or D2XXBackend()
        self.device = self.backend.open_device(port[4:])
        try:
            self.device.configure_fifo(read_timeout_ms=max(1,min(50,int(timeout*1000))),
                                       write_timeout_ms=max(1,int(timeout*1000)))
        except Exception:
            try:self.device.close()
            except Exception:pass
            raise
        self.parser = FrameParser()
        self.closed = False

    def _check_packets(self, packets):
        if self.closed:
            raise D2XXError('高速 USB 连接已关闭')
        if any(not isinstance(packet,bytes) or len(packet)!=32 for packet in packets):
            raise ValueError('USB 协议帧必须为 32 字节')

    def _write_all(self, data, deadline):
        offset=0
        while offset<len(data):
            sent=self.device.write(data[offset:])
            if type(sent) is not int or not 0<=sent<=len(data)-offset:
                raise D2XXError('FTDI 返回了无效的写入字节数')
            offset+=sent
            if offset<len(data) and time.monotonic()>=deadline:
                raise TimeoutError('高速 USB 写入未完成；操作结果未知，请重新连接并检查状态')
            if not sent:time.sleep(.001)

    def exchange(self, packet):
        self._check_packets([packet])
        for _ in range(self.retries):
            deadline=time.monotonic()+self.timeout
            self._write_all(packet,deadline)
            while time.monotonic() < deadline:
                for frame in self.parser.feed(self.device.read(4096)):
                    if frame[2] == 1 and frame[3] == (packet[3] | 0x80) and frame[4:6] == packet[4:6]:
                        return frame
        raise TimeoutError('高速 USB 未收到有效应答。请确认连接 USB SLAVE、烧录 USB FIFO 固件，且 FT232H EEPROM 已配置为 245 FIFO 模式；操作结果未知，请读取状态或重新连接')

    def exchange_many(self, packets):
        """Pipeline a bounded group of upload requests, verify every reply.

        Do not retry a window: the firmware's last-request cache cannot dedupe
        an older command after subsequent requests have already completed.
        The caller commits only after all windows have been acknowledged.
        """
        packets=tuple(packets)
        self._check_packets(packets)
        if not 1<=len(packets)<=UPLOAD_WINDOW:
            raise ValueError(f'高速 USB 批量窗口必须为 1～{UPLOAD_WINDOW} 帧')
        keys=[(packet[3]|0x80,packet[4:6]) for packet in packets]
        if len(set(keys))!=len(keys):
            raise ValueError('高速 USB 批量窗口含重复的命令序号')
        deadline=time.monotonic()+self.timeout
        self._write_all(b''.join(packets),deadline)
        pending=set(keys);received={}
        while pending and time.monotonic()<deadline:
            for frame in self.parser.feed(self.device.read(4096)):
                key=(frame[3],frame[4:6])
                if frame[2]==1 and key in pending:
                    received[key]=frame;pending.remove(key)
        if pending:
            raise TimeoutError(f'高速 USB 批量下载缺少 {len(pending)} 帧应答，未提交新波形；操作结果未知，请重新连接后重新准备。批量窗口不会自动重发')
        return [received[key] for key in keys]

    def close(self):
        if not self.closed:
            self.closed = True
            self.device.close()
