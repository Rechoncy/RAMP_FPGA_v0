"""Board configuration and conservative XDC generation; no guessed UART pins."""
from dataclasses import dataclass, field, asdict
import re
import math
from .uart_settings import BAUD_RATES, DEFAULT_BAUD
from .hardware_interfaces import (USB_PORTS,
    interface_ports, interface_io_standard, interface_purpose, validate_interfaces,
    interface_constraints)

LEGACY = {
    "sys_clk": "R4", "dipole_ramp_trigger": "F13", "fm_trigger": "F14",
    "gnd0": "F15", "gnd1": "F16", "dipole_ramp_clk": "Y8", "da_fm_clk": "R14",
    **{f"dipole_ramp[{i}]": p for i, p in enumerate(["V9", "W9", "Y9", "V8", "U7", "AB7", "V7", "AB6", "AB8", "AA8"])},
    **{f"fm_value[{i}]": p for i, p in enumerate(["U18", "U17", "V19", "T18", "V20", "R18", "N17", "P17", "R16", "P15"])},
    "uart_rx": "", "uart_tx": "",
}


MAX_DACS = MAX_TRIGGERS = 8
CLOCK_INPUTS = ('single_ended', 'differential')
SINGLE_CLOCK_STANDARDS = ('LVCMOS33', 'LVCMOS25', 'LVCMOS18', 'LVCMOS15', 'LVCMOS12')
DIFFERENTIAL_CLOCK_STANDARDS = ('LVDS_25', 'LVDS')
ALL_CLOCK_PORTS = ('sys_clk', 'sys_clk_p', 'sys_clk_n')
PORT_MIGRATION = {'dipole_ramp_trigger':'trigger_in[0]','fm_trigger':'trigger_in[1]',
                  'dipole_ramp_clk':'dac_clk[0]','da_fm_clk':'dac_clk[1]',
                  **{f'dipole_ramp[{i}]':f'dac_data[{i}]' for i in range(10)},
                  **{f'fm_value[{i}]':f'dac_data[{i+10}]' for i in range(10)}}
REFERENCE_PINS = {PORT_MIGRATION.get(k,k):v for k,v in LEGACY.items()}
# Additional DAC connections supplied in the reference XDC's mag_z / mag_y blocks.
REFERENCE_PINS.update({'dac_clk[2]':'E19','dac_clk[3]':'F21',
    **{f'dac_data[{20+i}]':p for i,p in enumerate(['N22','M22','M21','L21','K22','K21','J22','H22','G22','G21'])},
    **{f'dac_data[{30+i}]':p for i,p in enumerate(['N19','N18','M20','N20','J21','J20','G20','H20','L18','M18'])}})


def clock_ports(clock_input='single_ended'):
    if clock_input not in CLOCK_INPUTS:
        raise ValueError('参考时钟输入形式必须为单端或差分')
    return ('sys_clk_p', 'sys_clk_n') if clock_input == 'differential' else ('sys_clk',)


def clock_standard(board):
    if board.clock_io_standard:
        return board.clock_io_standard
    return 'LVDS_25' if board.clock_input == 'differential' else board.io_overrides.get('sys_clk', board.io_standard)


def clock_core_prefix(board):
    return 'core/' * (int(bool(board.boot_profiles)) + int(board.clock_input == 'differential'))


def ports_for_counts(dac_count, trigger_count, dac_bits=10, clock_input='single_ended'):
    return [*clock_ports(clock_input),'uart_rx','uart_tx','gnd0','gnd1',
            *[f'trigger_in[{i}]' for i in range(trigger_count)],
            *[p for i in range(dac_count) for p in [f'dac_clk[{i}]',*[f'dac_data[{i*dac_bits+j}]' for j in range(dac_bits)]]]]


def ports_for_board(board):
    return ports_for_counts(board.dac_count, board.trigger_count, board.dac_bits, board.clock_input) + interface_ports(board)


def port_purpose(port, dac_bits=10):
    if port in USB_PORTS: return interface_purpose(port)
    if port.startswith('dac_data['):
        i=int(port[9:-1]); return f'DAC {i//dac_bits+1} · 数据 D{i%dac_bits}'
    if port.startswith('dac_clk['): return f'DAC {int(port[8:-1])+1} · 输出采样时钟'
    if port.startswith('trigger_in['): return f'触发输入 {int(port[11:-1])+1} · 可路由至所选 DAC'
    return {'sys_clk':'单端参考时钟','sys_clk_p':'差分参考时钟 · P 正端','sys_clk_n':'差分参考时钟 · N 负端',
            'uart_rx':'UART 接收','uart_tx':'UART 发送','gnd0':"固定低电平控制输出 1 · 1'b0（非 GND）",'gnd1':"固定低电平控制输出 2 · 1'b0（非 GND）"}[port]


def remap_data_ports(mapping, old_bits, new_bits):
    """Keep physical DAC number/bit identity when the flat bus stride changes."""
    result={k:v for k,v in mapping.items() if not k.startswith('dac_data[')}
    for port,value in mapping.items():
        if port.startswith('dac_data['):
            channel,bit=divmod(int(port[9:-1]),old_bits)
            if bit<new_bits: result[f'dac_data[{channel*new_bits+bit}]']=value
    return result


@dataclass
class Board:
    name: str = "Artix-7 · 参考引脚模板"
    part: str = "xc7a35tfgg484-2"
    ref_mhz: float = 50.0
    core_mhz: float = 200.0
    vivado: str = r"E:\Software\Xilinx\Vivado\2023.1\bin\vivado.bat"
    io_standard: str = "LVCMOS33"
    io_overrides: dict = field(default_factory=dict)
    cfgbvs: str = "VCCO"
    config_voltage: float = 3.3
    pins: dict = field(default_factory=lambda: dict(REFERENCE_PINS))
    dac_bits: int = 10
    dac_count: int = 2
    trigger_count: int = 2
    dac_timing: dict = field(default_factory=dict)
    dac_delay_min: float | None = None
    dac_delay_max: float | None = None
    dac_falling: bool = False
    flash: dict = field(default_factory=dict)
    boot_profiles: dict = field(default_factory=dict)
    schema: int = 3
    clock_input: str = 'single_ended'
    clock_io_standard: str = ''
    clock_diff_term: bool = False
    uart_baud: int = DEFAULT_BAUD
    usb_fifo: dict = field(default_factory=dict)
    dac_model: str = ''

    def __post_init__(self):
        from .flash import FlashSettings
        FlashSettings.from_dict(self.flash).validate()
        if not isinstance(self.boot_profiles,dict):raise ValueError('上电方案必须是按 DAC 编号索引的对象')
        if self.dac_bits != 10 and self.pins == REFERENCE_PINS:
            self.pins = remap_data_ports(self.pins,10,self.dac_bits)

    def to_dict(self):
        return asdict(self)

    @classmethod
    def from_dict(cls, value):
        if value.get("schema") not in (1,2,3):
            raise ValueError("不支持的板卡配置版本")
        value=dict(value)
        retired=value.pop('ddr3', None)
        if isinstance(retired,dict) and retired.get('enabled'):
            raise ValueError('此测试配置启用了已移除的 DDR3；请使用 BRAM 板卡配置，不能将扩展容量当作可用容量')
        if value['schema']==1:
            for field_name in ('pins','io_overrides'):
                original=value.get(field_name,{})
                value[field_name]={PORT_MIGRATION.get(k,k):v for k,v in original.items()}
        value['schema']=3
        board = cls(**value)
        return board


def validate(board, complete=False):
    errors = []
    if not re.fullmatch(r"xc7[akvzs][a-zA-Z0-9-]{3,60}", board.part):
        errors.append("当前构建适配器支持 Xilinx 7 系列（xc7a/k/v/z/s）；其他系列需要新增时钟与 IO 适配器")
    if not isinstance(board.ref_mhz, (int, float)) or not math.isfinite(board.ref_mhz) or not 10 <= board.ref_mhz <= 500:
        errors.append("参考时钟须为 10–500 MHz；实际范围还需 Clocking Wizard 校验")
    if board.core_mhz not in (100, 125, 200):
        errors.append("输出采样率须为 100、125 或 200 MSPS")
    if board.dac_model not in ('', '3PD5651E'):
        errors.append('不支持的 DAC 型号预设')
    if board.dac_model == '3PD5651E':
        if board.core_mhz not in (100, 125):
            errors.append('3PD5651E 最高 125 MSPS；请选择 100 或 125 MSPS')
        if board.dac_bits != 10:
            errors.append('3PD5651E 使用 10 位接口；不会自动缩放已保存方案的原码')
    if type(board.uart_baud) is not int or board.uart_baud not in BAUD_RATES:
        errors.append("固件 UART 波特率须为支持的档位（9600～1000000 baud）")
    if board.clock_input not in CLOCK_INPUTS:
        errors.append('参考时钟输入形式必须为单端或差分')
    else:
        allowed = DIFFERENTIAL_CLOCK_STANDARDS if board.clock_input == 'differential' else SINGLE_CLOCK_STANDARDS
        if clock_standard(board) not in allowed:
            errors.append('参考时钟 IO 标准与输入形式不匹配')
        for port in clock_ports(board.clock_input):
            if board.io_overrides.get(port) and board.io_overrides[port] != clock_standard(board):
                errors.append(f'{port} IO 标准冲突；请在板卡信息中统一设置参考时钟 IO 标准')
    if type(board.clock_diff_term) is not bool:
        errors.append('差分时钟内部终端设置必须为布尔值')
    elif board.clock_input == 'single_ended' and board.clock_diff_term:
        errors.append('单端时钟不适用差分内部终端')
    if board.io_standard not in ("LVCMOS33", "LVCMOS25", "LVCMOS18"):
        errors.append("不支持的 IO 标准")
    if board.cfgbvs not in ("VCCO","GND") or board.config_voltage not in (1.8,2.5,3.3):
        errors.append("配置 Bank 的 CFGBVS/CONFIG_VOLTAGE 值无效")
    if type(board.dac_bits) is not int or board.dac_bits not in (10,12,14):
        errors.append('DAC 位数必须为 10、12 或 14')
    if type(board.dac_count) is not int or not 1<=board.dac_count<=MAX_DACS:
        errors.append('DAC 数量必须为 1～8')
    if type(board.trigger_count) is not int or not 1<=board.trigger_count<=MAX_TRIGGERS:
        errors.append('触发输入数量必须为 1～8')
    if errors: return errors
    errors.extend(validate_interfaces(board, complete))
    if errors: return errors
    used = {}
    for port in ports_for_board(board):
        if port in USB_PORTS:
            if interface_io_standard(board, port) not in ("LVCMOS33", "LVCMOS25", "LVCMOS18"): errors.append(f'{port} USB FIFO IO 标准无效')
        elif port not in ALL_CLOCK_PORTS and board.io_overrides.get(port, board.io_standard) not in ("LVCMOS33", "LVCMOS25", "LVCMOS18"):
            errors.append(f"{port} IO 标准无效")
        pin = board.pins.get(port, "").strip().upper()
        if not pin:
            if complete:
                errors.append(f"缺少 {port} 管脚")
        elif not re.fullmatch(r"[A-Z]{1,3}[1-9][0-9]{0,2}", pin):
            errors.append(f"{port} 管脚格式无效")
        elif pin in used:
            errors.append(f"管脚 {pin} 同时分配给 {used[pin]} 和 {port}")
        else:
            used[pin] = port
    for i in range(board.dac_count):
        try: low,high,falling=effective_timing(board,i)
        except (TypeError,ValueError):
            errors.append(f'DAC {i+1} 时序配置格式无效'); continue
        if type(falling) is not bool: errors.append(f'DAC {i+1} 采样边沿无效')
        for label,v in [('最小延迟',low),('最大延迟',high)]:
            if v is not None and (type(v) not in (int,float) or not math.isfinite(v) or abs(v)>100):
                errors.append(f'DAC {i+1} {label}须为 −100～100 ns 的有限数值')
            elif complete and v is None: errors.append(f'缺少 DAC {i+1} {label}，请依据建立/保持时间与走线延迟填写')
        if type(low) in (int,float) and type(high) in (int,float) and low>high:
            errors.append(f'DAC {i+1} 最小延迟不能大于最大延迟')
    return errors


def effective_timing(board,index):
    if not isinstance(board.dac_timing,dict): raise ValueError('invalid timing map')
    item=board.dac_timing.get(str(index),{})
    if not isinstance(item,dict): raise ValueError('invalid DAC timing')
    return (item.get('min',board.dac_delay_min),item.get('max',board.dac_delay_max),item.get('falling',board.dac_falling))


def generate_xdc(board, complete=False):
    errors = validate(board, complete)
    if errors:
        raise ValueError("\n".join(errors))
    from .flash import FlashSettings
    flash=FlashSettings.from_dict(board.flash);flash.validate()
    lines = ["# Generated by Ramp Studio. Verify electrical compatibility against the board schematic.",
             "# Core clock is generated by Clocking Wizard; input clock remains the physical oscillator frequency.",
             f"create_clock -name sys_clk -period {1000 / board.ref_mhz:.6f} [get_ports {{{clock_ports(board.clock_input)[0]}}}]",
             f"set_property CFGBVS {board.cfgbvs} [current_design]",
             f"set_property CONFIG_VOLTAGE {board.config_voltage:g} [current_design]",
             f"set_property CONFIG_MODE {flash.interface} [current_design]",
             f"set_property BITSTREAM.CONFIG.SPI_BUSWIDTH {flash.interface[-1]} [current_design]",
             f"set_property BITSTREAM.CONFIG.CONFIGRATE {flash.config_rate} [current_design]",
             f"set_property BITSTREAM.CONFIG.SPI_FALL_EDGE {'Yes' if flash.falling_edge else 'No'} [current_design]"]
    group = None
    for port in ports_for_board(board):
        if port in ALL_CLOCK_PORTS: current_group = 'Reference clock'
        elif port in ('gnd0', 'gnd1'): current_group = 'Constant-low control outputs (RTL drives 1\'b0; not ground pins)'
        elif port in USB_PORTS: current_group = 'FT232H USB FIFO (15 pins; independent IO standard)'
        elif port.startswith('trigger_in['): current_group = 'External triggers'
        elif port.startswith('dac_'):
            channel = int(port.split('[')[1][:-1])
            if port.startswith('dac_data['): channel //= board.dac_bits
            current_group = f'DAC {channel + 1}'
        else: current_group = 'UART and auxiliary outputs'
        if current_group != group:
            lines += ['', '# Interface: ' + current_group]
            group = current_group
        pin = board.pins.get(port, "").strip().upper()
        standard = (clock_standard(board) if port in ALL_CLOCK_PORTS else
                    interface_io_standard(board, port) if port in USB_PORTS else board.io_overrides.get(port, board.io_standard))
        if pin:
            lines.append(f"set_property -dict {{PACKAGE_PIN {pin} IOSTANDARD {standard}}} [get_ports {{{port}}}]")
        else:
            lines.append(f"# TODO: unassigned port {port}")
            lines.append(f"set_property IOSTANDARD {standard} [get_ports {{{port}}}]")
    if board.clock_input == 'differential':
        lines += ['# P/N must be one physical clock-capable differential pair; only P defines create_clock.',
                  f"set_property DIFF_TERM {'TRUE' if board.clock_diff_term else 'FALSE'} [get_ports {{sys_clk_p sys_clk_n}}]",
                  'set_property IBUF_LOW_PWR FALSE [get_ports {sys_clk_p sys_clk_n}]']
    lines += interface_constraints(board)
    # Only the asynchronous input -> first synchronizer stage is excluded.
    lines += [
        "set_false_path -from [get_ports {trigger_in[*] uart_rx}] -to [get_pins -hier -filter {NAME =~ */sync_ff_reg[0]/D}]",
        "# UART TX is asynchronous to the receiver's independent baud clock.",
        "set_false_path -to [get_ports uart_tx]",
        f"# Each ODDR-forwarded DAC clock uses the shared {board.core_mhz:g} MHz MMCM output.",
    ]
    for i in range(board.dac_count):
        clock=f'dac_clk_{i+1}'
        prefix=clock_core_prefix(board)
        lines.append(f'create_generated_clock -name {clock} -source [get_pins {prefix}u_clock/u_ip/inst/mmcm_adv_inst/CLKOUT0] -divide_by 1 [get_ports {{dac_clk[{i}]}}]')
        low,high,falling=effective_timing(board,i)
        if low is not None and high is not None:
            edge=' -clock_fall' if falling else ''
            bus=' '.join(f'dac_data[{i*board.dac_bits+j}]' for j in range(board.dac_bits))
            for kind,value in [('min',low),('max',high)]:
                lines.append(f'set_output_delay -clock [get_clocks {clock}]{edge} -{kind} {value:.6f} [get_ports {{{bus}}}]')
        else: lines.append(f'# TODO: DAC {i+1} output min/max delay constraints are missing. Draft only.')
    return '\n'.join(lines) + '\n'
