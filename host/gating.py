"""Compile independent entry/return tables; the high level supplies the hold."""
from dataclasses import dataclass,replace
from .functions import HZ,MAX_RUNS,MAX_SAMPLES,validate_shape,stage_counts,shape_key,compile_range,compiler_capacity


@dataclass(frozen=True)
class GateSamples:
    runs:tuple
    total:int
    max_error:float
    rise_runs:int
    rise_ticks:int
    fall_ticks:int
    interpolation:bool=False
    error_limit:int|None=None
    checked_samples:int=0


def validate_gate(p, sample_hz=HZ):
    if p.trigger!='external' or p.trigger_edge!='rising':
        raise ValueError('电平门控使用外部输入：上升沿进入、下降沿返回')
    if p.mode not in ('ramp','function') or p.mode=='function' and p.function_layout!='staged':
        raise ValueError('电平门控仅支持单幅度 Ramp 和三段函数')
    q=replace(p,timing_mode='fixed',hold_ms=0)
    validate_shape(q,sample_hz)
    up,_,down=stage_counts(q,sample_hz)
    if up<1 or down<1:raise ValueError(f'门控进入和返回时长分别至少为一个 {1e9/sample_hz:g} ns 周期')
    if up+down>min(MAX_SAMPLES,sample_hz):raise ValueError('进入与返回的总时长不能超过 1 秒')
    return up+down


def compile_gate(p, *, max_runs=None, min_ticks=32, sample_hz=HZ):
    n=validate_gate(p,sample_hz)
    up,_,down=stage_counts(replace(p,hold_ms=0),sample_hz)
    q=replace(p,timing_mode='fixed',hold_ms=0,rise_ms=up*1000/sample_hz,fall_ms=down*1000/sample_hz)
    key=shape_key(q,sample_hz)
    if p.playback == 'linear':
        from .interpolation import compile_interval, DEFAULT_MAX_RUNS
        capacity=DEFAULT_MAX_RUNS if max_runs is None else max_runs
        a=compile_interval(key,n,0,up,p.initial,p.amplitude,p.interpolation_error,capacity,min_ticks)
        if len(a.runs)>=capacity:
            raise ValueError(f'门控进入已占 {len(a.runs)} 个插值段，返回段超过本通道 {capacity} 段容量')
        b=compile_interval(key,n,up,down,p.amplitude,p.initial,p.interpolation_error,capacity-len(a.runs),min_ticks)
        return GateSamples(a.runs+b.runs,n,max(a.max_error,b.max_error),len(a.runs),up,down,
                           True,p.interpolation_error,n)
    if p.playback != 'hold':raise ValueError('不支持的播放模式')
    capacity=compiler_capacity(max_runs)
    a=compile_range(key,n,0,up,max_runs=capacity); b=compile_range(key,n,up,down,max_runs=capacity)
    if len(a.runs)+len(b.runs)>capacity:raise ValueError(f'门控进入和返回合计超过 {capacity} 个连续码段')
    # Deliberately keep the phase boundary even when both codes are identical.
    return GateSamples(a.runs+b.runs,n,max(a.max_error,b.max_error),len(a.runs),up,down)


def preview_profile(p, sample_hz=HZ):
    validate_gate(p,sample_hz)
    # Display a labeled illustrative plateau, never upload a timed hold.
    hold=min((p.rise_ms+p.fall_ms)/2,max(0,1000-p.rise_ms-p.fall_ms))
    return replace(p,timing_mode='fixed',hold_ms=hold)
