"""Event-indexed DAC scans. No time sampling or waveform RAM is required."""
from dataclasses import dataclass
from .model import integer,Capabilities

SCAN_FORMAT='<HHHhHIBBB'


def validate_scan(p,caps):
    if not caps.trigger_scan:raise ValueError('当前固件不支持触发步进扫描，请在板卡页面重新生成并升级固件')
    limit=(1<<p.dac_bits)-1
    integer(p.scan_start,'扫描起始码',0,limit)
    integer(p.scan_step,'扫描步长',-limit,limit)
    integer(p.scan_points,'扫描点数',1,65535)
    if p.scan_end not in ('hold','loop'):raise ValueError('不支持的扫描末点行为')
    if p.repeat!=1 or p.timing_mode!='fixed':raise ValueError('触发步进使用独立的末点行为，不支持波形重复次数或电平门控')
    integer(p.channel,'输出 DAC 编号',0,caps.dac_count-1)
    integer(p.trigger_input,'触发输入编号',0,caps.trigger_count-1 if p.trigger=='external' else 7)
    if p.trigger_edge not in ('rising','falling'):raise ValueError('不支持的触发边沿')
    last=p.scan_start+(p.scan_points-1)*p.scan_step
    if not 0<=last<=limit:raise ValueError(f'扫描末点 {last} 超出 {p.dac_bits} 位 DAC 原码范围 0～{limit}，请调整步长或点数')
    return last


@dataclass(frozen=True)
class CompiledScan:
    runs: tuple = ()
    total: int = 0
    max_error: float = 0


def compile_scan(p):
    from .model import compile_profile
    compile_profile(p,Capabilities(dac_bits=p.dac_bits,trigger_scan=True))
    return CompiledScan()


def preview_scan(p):
    """Axes are trigger numbers, with 0 denoting the armed initial value."""
    import numpy as np
    compile_scan(p)
    indices=np.unique(np.linspace(0,p.scan_points-1,min(p.scan_points,1200),dtype=int))
    times=[0.];values=[p.initial]
    for index in indices:
        times.extend([float(index+1),float(index+1)])
        values.extend([values[-1],p.scan_start+int(index)*p.scan_step])
    times.append(float(p.scan_points)+.35);values.append(values[-1])
    return times,values,times[-1],'scan'
