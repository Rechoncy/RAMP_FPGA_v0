"""Saved waveform snapshots assembled into hardware timelines or event programs."""
from dataclasses import dataclass, replace
from decimal import Decimal, ROUND_HALF_UP
from functools import lru_cache
import json
import struct
import numpy as np
from .model import Profile, Capabilities, offline_capabilities, compile_profile, integer, fingerprint
from .sample_rates import validate_sample_hz, supports_sample_clock

HZ=200_000_000
MAX_STEPS=64
MAX_TICKS=(1<<48)-1
STEP_FORMAT='<BBBBHHHHIIHH'


def has_interpolation(p):
    return p.playback == 'linear' if p.mode != 'sequence' else any(
        isinstance(s,dict) and isinstance(s.get('waveform'),dict)
        and s['waveform'].get('playback','hold') == 'linear' for s in p.sequence_steps)


def ticks(ms, sample_hz=HZ):
    validate_sample_hz(sample_hz)
    if isinstance(ms,bool):raise ValueError('时间必须是非负有限数值')
    try:
        value=Decimal(str(ms))
        if not value.is_finite() or value<0:raise ValueError()
        n=int((value*sample_hz/1000).to_integral_value(rounding=ROUND_HALF_UP))
    except Exception as exc:raise ValueError('时间必须是非负有限数值') from exc
    if n>MAX_TICKS:raise ValueError('序列时长超出 48 位时间范围')
    if value>0 and n==0:raise ValueError(f'非零时间不足一个 {1e9/sample_hz:g} ns 周期')
    return n


def waveform(step,p):
    data=step.get('waveform')
    if not isinstance(data,dict) or data.get('mode') not in ('ramp','function'):
        raise ValueError('步骤必须引用已保存 Ramp 或函数快照，不能嵌套序列')
    w=Profile.from_dict(data)
    if w.dac_bits!=p.dac_bits or w.initial!=p.initial:
        raise ValueError(f'波形“{w.name}”的位数/初始码必须与序列一致')
    gate=step.get('kind')=='gate' and p.sequence_mode=='events'
    if w.timing_mode=='gate' and not gate:
        raise ValueError('门控波形只能用于门控步骤')
    return replace(w,repeat=1,channel=p.channel,safe=p.safe,timing_mode='gate' if gate else 'fixed',
                   trigger='external' if gate else 'software',trigger_input=step.get('input',0),trigger_edge='rising')


def validate_sequence(p,caps):
    sample_hz=caps.core_hz
    if p.sequence_mode not in ('timeline','events'):raise ValueError('未知序列类型')
    if not isinstance(p.sequence_steps,list) or not 1<=len(p.sequence_steps)<=MAX_STEPS:
        raise ValueError('执行序列需要 1～64 个步骤')
    if p.timing_mode!='fixed':raise ValueError('序列的门控由步骤设置')
    modes={s.get('waveform',{}).get('playback','hold') for s in p.sequence_steps if isinstance(s,dict) and isinstance(s.get('waveform'),dict)}
    if p.sequence_mode=='timeline' and len(modes)>1:
        raise ValueError('固定时间表不能混合逐码保持与线性插值波形；请使用相同播放模式，或改为事件序列')
    if 'linear' in modes and not caps.interpolation:
        raise ValueError('当前固件不支持分段线性插值事件/时间表，请生成并烧录新版固件')
    if not supports_sample_clock(caps):raise ValueError('序列需要受支持的采样播放器')
    integer(p.channel,'DAC 通道',0,caps.dac_count-1)
    if p.sequence_mode=='events':
        if not caps.sequence_steps:raise ValueError('事件/门控步骤需要新版 FPGA 序列固件；固定时间表仍可使用')
        if len(p.sequence_steps)>caps.sequence_steps:raise ValueError('步骤数量超过设备容量')
        if p.trigger!='external' or p.repeat!=1:raise ValueError('事件序列使用步骤触发并自动循环等待；整表次数固定为 1')
        if p.sequence_steps[0].get('kind') not in ('edge','gate'):raise ValueError('事件序列第一步必须等待边沿或门控')
    elif p.trigger=='external':
        integer(p.trigger_input,'起始触发输入',0,caps.trigger_count-1)
        if p.trigger_edge not in ('rising','falling'):raise ValueError('未知触发边沿')
        if not caps.trigger_routing and (p.trigger_input!=p.channel or p.trigger_edge!='rising'):raise ValueError('旧固件不支持此触发路由')
    end=0
    for i,s in enumerate(p.sequence_steps):
        if not isinstance(s,dict):raise ValueError('无效步骤')
        kind=s.get('kind','edge')
        if kind not in ('edge','gate','delay'):raise ValueError('未知步骤开始条件')
        integer(s.get('repeat',1),f'步骤 {i+1} 重复次数',1,65535)
        if p.sequence_mode=='events':
            integer(s.get('input',0),'步骤触发输入',0,caps.trigger_count-1)
            if s.get('edge','rising') not in ('rising','falling'):raise ValueError('未知步骤边沿')
            if kind=='gate' and (s.get('edge','rising')!='rising' or s.get('repeat',1)!=1):raise ValueError('门控步骤使用上升进入/下降返回，重复次数为 1')
            if ticks(s.get('delay_ms',0),sample_hz)>0xffffffff:raise ValueError(f'步骤延时最多 {0xffffffff/sample_hz:.9g} s')
        elif kind=='gate':raise ValueError('固定时间表不能包含未知保持时长的门控步骤')
        w=waveform(s,p)
        c=compile_profile(w,replace(caps,sequence_steps=MAX_STEPS,gate_mode=True))
        duration=round(c.duration_ms*sample_hz/1000)*s.get('repeat',1)
        if p.sequence_mode=='timeline':
            start=ticks(s.get('start_ms',0),sample_hz)
            if start<end:raise ValueError(f'步骤 {i+1} 与前一步在量化后重叠；开始时间按表内顺序递增')
            end=start+duration
        else:end+=duration+ticks(s.get('delay_ms',0),sample_hz)
        if end>MAX_TICKS:raise ValueError('序列总时长超限')
    return end


@dataclass(frozen=True)
class SequenceSamples:
    runs: tuple
    total: int
    max_error: float
    steps: tuple=()
    boundaries: tuple=()
    interpolation: bool=False
    error_limit: int|None=None
    checked_samples: int=0


@lru_cache(maxsize=4)
def _compile(serialized,max_runs,min_ticks,sample_hz):
    from .functions import compile_samples,compiler_capacity
    p=Profile.from_dict(json.loads(serialized))
    validate_sequence(p,offline_capabilities(core_hz=sample_hz,dac_bits=p.dac_bits,sequence_steps=64))
    linear=has_interpolation(p)
    # Keep the old hold compiler's counting behavior: hardware capacity is
    # assessed afterwards, so callers can report the exact required pool size.
    capacity=(2048 if max_runs is None else max_runs) if linear else compiler_capacity(max_runs)
    runs=[];steps=[];boundaries=[];cursor=0;error=0.;pool={};limit=0;checked=0
    def check_capacity():
        if len(runs)>capacity:
            kind='插值/保持波形池' if linear else '波形池'
            raise ValueError(f'{kind}需要超过 {capacity} 个段槽位，超过本通道容量；不会放宽精度')
    def append(code,n):
        while n:
            if runs and runs[-1][0]==code and runs[-1][1]<0xffffffff:
                take=min(n,0xffffffff-runs[-1][1]);runs[-1]=(code,runs[-1][1]+take)
            else:
                take=min(n,0xffffffff);runs.append((code,take))
            n-=take
            if len(runs)>capacity:raise ValueError(f'序列超过编译器 {capacity} 码段上限；实际下载另按设备容量检查')
    def append_wait(n):
        if n and n<min_ticks:
            raise ValueError(f'线性时间表空隙仅 {n} 拍，最短段为 {min_ticks} 拍；不会延长时间表')
        while n:
            take=min(n,0xffffffff)
            if 0<n-take<min_ticks:take-=min_ticks-(n-take)
            runs.append((p.initial,take));n-=take;check_capacity()
    for i,s in enumerate(p.sequence_steps):
        w=waveform(s,p);data=compile_samples(w,max_runs=capacity,min_ticks=min_ticks,sample_hz=sample_hz)
        if data.interpolation:
            error=max(error,data.max_error);limit=max(limit,data.error_limit)
        elif not linear:error=max(error,data.max_error)
        checked+=data.total*(s.get('repeat',1) if p.sequence_mode=='timeline' else 1)
        if p.sequence_mode=='timeline':
            start=ticks(s.get('start_ms',0),sample_hz)
            if data.interpolation:
                if data.runs[-1][0]!=p.initial:
                    raise ValueError('线性时间表中的波形终点必须回到序列初始码，才能无跳拍衔接下一波形')
                append_wait(start-cursor)
                checked+=start-cursor
                for _ in range(s.get('repeat',1)):
                    runs.extend(data.runs);check_capacity()
            else:
                append(p.initial,start-cursor)
                for _ in range(s.get('repeat',1)):
                    for code,n in data.runs:append(code,n)
            cursor=start+data.total*s.get('repeat',1);boundaries.append((start,cursor,w.name))
        else:
            key=(data.runs,w.playback,w.initial,w.timing_mode,getattr(data,'rise_runs',0))
            if key not in pool:
                pool[key]=len(runs);runs.extend(data.runs)
            base=pool[key]
            check_capacity()
            steps.append((i,{'edge':0,'gate':1,'delay':2}[s['kind']],s.get('input',0),int(s.get('edge','rising')=='falling'),
                          base,len(data.runs),getattr(data,'rise_runs',0),w.amplitude,
                          getattr(data,'fall_ticks',1),ticks(s.get('delay_ms',0),sample_hz),s.get('repeat',1),int(data.interpolation)))
            cursor+=data.total*s.get('repeat',1)
    return SequenceSamples(tuple(runs),cursor,error,tuple(steps),tuple(boundaries),linear,limit if linear else None,checked if linear else 0)


def compile_sequence(p, *, max_runs=None, min_ticks=32, sample_hz=HZ):return _compile(fingerprint(p),max_runs,min_ticks,validate_sample_hz(sample_hz))


def preview_sequence(p, sample_hz=HZ):
    """Bounded preview, independent of full compilation and descriptor capacity."""
    from .functions import preview_samples
    from .gating import preview_profile
    times=[];raw=[];codes=[];cursor=0
    for s in p.sequence_steps:
        w=waveform(s,p)
        if w.timing_mode=='gate':w=preview_profile(w,sample_hz)
        ts,rs,cs,_=preview_samples(w,sample_hz)
        start=float(s.get('start_ms',0)) if p.sequence_mode=='timeline' else cursor+float(s.get('delay_ms',0))
        if start>cursor:times.extend([cursor,start]);raw.extend([p.initial]*2);codes.extend([p.initial]*2)
        duration=compile_profile(w,offline_capabilities(core_hz=sample_hz,dac_bits=p.dac_bits)).duration_ms
        # At most 8 visual repetitions; preserve the true time extent and label.
        count=s.get('repeat',1)
        for repeat in range(min(count,8)):
            offset=start+repeat*duration
            times.extend(ts+offset);raw.extend(rs);codes.extend(cs)
        cursor=start+duration*count
        times.append(cursor);raw.append(p.initial);codes.append(p.initial)
    return np.array(times),np.array(raw),np.array(codes),cursor


def pack_step(step):return struct.pack(STEP_FORMAT,*step)
