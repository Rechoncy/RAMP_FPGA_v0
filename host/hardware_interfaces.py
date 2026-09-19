"""Portable optional hardware interfaces. Pin plans are not measured capabilities.

FT232H timing defaults come from DS_FT232H table 4.1 (synchronous FIFO).
"""
from dataclasses import dataclass, asdict, replace
import math


USB_PORTS = ('usb_clk', *[f'usb_data[{i}]' for i in range(8)],
             'usb_rxf_n', 'usb_txe_n', 'usb_rd_n', 'usb_wr_n', 'usb_oe_n', 'usb_siwu_n')
@dataclass
class UsbFifoSettings:
    enabled: bool = False
    clock_mhz: float = 60.0
    io_standard: str = 'LVCMOS33'
    input_delay_min: float = 0.0
    input_delay_max: float = 9.0
    output_delay_min: float = 0.0
    output_delay_max: float = 7.5

    @classmethod
    def from_dict(cls, value):
        if not isinstance(value, dict):
            raise ValueError('USB FIFO 设置必须是对象')
        try: return cls(**value)
        except TypeError as exc: raise ValueError('USB FIFO 配置字段无效') from exc

    def to_dict(self):
        return asdict(self)


def interface_ports(board):
    usb = UsbFifoSettings.from_dict(board.usb_fifo)
    return list(USB_PORTS) if usb.enabled else []


def interface_io_standard(board, port):
    explicit = board.io_overrides.get(port)
    if explicit:
        return explicit
    if port in USB_PORTS:
        return UsbFifoSettings.from_dict(board.usb_fifo).io_standard
    raise ValueError(f'未知扩展接口 {port}')


def interface_purpose(port):
    return {'usb_clk': 'USB FIFO · 输入 · FT232H 60 MHz 时钟',
            'usb_rxf_n': 'USB FIFO · 输入 · 接收非空，低有效',
            'usb_txe_n': 'USB FIFO · 输入 · 发送可写，低有效',
            'usb_rd_n': 'USB FIFO · 输出 · 读使能，低有效',
            'usb_wr_n': 'USB FIFO · 输出 · 写使能，低有效',
            'usb_oe_n': 'USB FIFO · 输出 · 芯片驱动总线，低有效',
            'usb_siwu_n': 'USB FIFO · 输出 · 发送立即刷新/唤醒'} .get(port, 'USB FIFO · 双向 · 数据 D' + port[9:-1])


def validate_interfaces(board, complete=False):
    errors = []
    try:
        usb = UsbFifoSettings.from_dict(board.usb_fifo)
    except ValueError as exc:
        return [str(exc)]
    for title, enabled in [('USB FIFO', usb.enabled)]:
        if type(enabled) is not bool: errors.append(f'{title} 启用设置必须为布尔值')
    if usb.enabled:
        if type(usb.clock_mhz) not in (int, float) or usb.clock_mhz != 60:
            errors.append('FT232H 同步 FIFO 时钟固定为 60 MHz')
        if usb.io_standard not in ('LVCMOS33', 'LVCMOS25', 'LVCMOS18'):
            errors.append('USB FIFO IO 标准无效')
        for name in ('input_delay_min', 'input_delay_max', 'output_delay_min', 'output_delay_max'):
            value = getattr(usb, name)
            if type(value) not in (int, float) or not math.isfinite(value) or abs(value) > 100:
                errors.append(f'USB FIFO {name} 须为 −100～100 ns 的有限数值')
        if not errors:
            for prefix in ('input', 'output'):
                if getattr(usb, prefix + '_delay_min') > getattr(usb, prefix + '_delay_max'):
                    errors.append('USB FIFO 最小延迟不能大于最大延迟')
    return errors


def interface_constraints(board):
    """FT232H timing is independent of UART and the 200 MHz playback clock."""
    lines = []
    usb = UsbFifoSettings.from_dict(board.usb_fifo)
    if usb.enabled:
        lines += ['', '# Interface: FT232H USB FIFO timing (60 MHz)',
                  '# FT232H synchronous FIFO, DS_FT232H table 4.1; include PCB skew in configured delays.',
                  'create_clock -name usb_clk -period 16.666667 [get_ports {usb_clk}]',
                  '# Fast output transitions are required for the 60 MHz FIFO bus; timing bounds are unchanged.',
                  'set_property SLEW FAST [get_ports {usb_data[*] usb_rd_n usb_wr_n usb_oe_n}]']
        for kind in ('min', 'max'):
            lines.append(f'set_input_delay -clock [get_clocks usb_clk] -{kind} {getattr(usb, "input_delay_" + kind):.6f} [get_ports {{usb_data[*] usb_rxf_n usb_txe_n}}]')
            lines.append(f'set_output_delay -clock [get_clocks usb_clk] -{kind} {getattr(usb, "output_delay_" + kind):.6f} [get_ports {{usb_data[*] usb_rd_n usb_wr_n usb_oe_n}}]')
        lines += ['# SIWU is held inactive by the transport; it is not the FIFO transfer clock.',
                  'set_false_path -to [get_ports {usb_siwu_n}]',
                  '# USB/core CDC is constrained by the asynchronous FIFO implementation.']
    return lines


DAVINCI_USB_PINS = {'usb_clk': 'Y4', 'usb_rxf_n': 'W1', 'usb_txe_n': 'AA5',
    'usb_rd_n': 'V4', 'usb_wr_n': 'Y3', 'usb_oe_n': 'U3', 'usb_siwu_n': 'V3',
    **{f'usb_data[{i}]': pin for i, pin in enumerate(('AB5', 'AA4', 'AB3', 'AA3', 'AB2', 'AB1', 'AA1', 'Y1'))}}
def apply_davinci_interfaces(board):
    """Explicit template application is atomic; never overwrite other interfaces."""
    from .board import validate
    if board.part.lower() != 'xc7a35tfgg484-2':
        raise ValueError('达芬奇 V2.1 模板仅适用于 xc7a35tfgg484-2；其他板卡请手工配置')
    pins, overrides = dict(board.pins), dict(board.io_overrides)
    chosen = DAVINCI_USB_PINS
    for port, pin in chosen.items():
        old = pins.get(port, '').strip().upper()
        if old and old != pin:
            raise ValueError(f'{port} 已有引脚 {old}，模板为 {pin}；请先清空该项再应用')
        pins[port] = pin
        overrides.pop(port, None)
    usb_settings = UsbFifoSettings.from_dict(board.usb_fifo)
    usb_settings.enabled = True
    usb_settings.io_standard = 'LVCMOS33'  # DaVinci V2.1 Bank 34 VCCO.
    updated = replace(board, pins=pins, io_overrides=overrides, usb_fifo=usb_settings.to_dict())
    errors = validate(updated)
    if errors: raise ValueError('\n'.join(errors))
    return updated
