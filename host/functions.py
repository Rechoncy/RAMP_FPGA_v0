"""Restricted vector expressions and exact firmware-clock, configurable-width, lossless RLE compilation."""
import ast
import math
from dataclasses import dataclass
from functools import lru_cache
import numpy as np
from .compiler_control import check_cancelled
from .sample_rates import validate_sample_hz, sample_period_ns

HZ = 200_000_000
MAX_RUNS = 8192
PROTOCOL_MAX_RUNS = 65535
MAX_SAMPLES = 200_000_000  # one second per custom-function shot; bounded computation
FUNCTIONS = {n: getattr(np, n) for n in ('sin','cos','tan','exp','sqrt','log','abs','minimum','maximum','where','clip')}
FUNCTIONS['min'] = np.minimum
FUNCTIONS['max'] = np.maximum

STAGED_TEMPLATES = {
    '高斯': ('I + (A-I)*(exp(-4.5*(1-t/T)**2)-exp(-4.5))/(1-exp(-4.5))',
             'I + (A-I)*(exp(-4.5*(t/T)**2)-exp(-4.5))/(1-exp(-4.5))'),
    '余弦平滑': ('I + (A-I)*(1-cos(pi*t/T))/2', 'I + (A-I)*(1+cos(pi*t/T))/2'),
    '线性': ('I + (A-I)*t/T', 'A + (I-A)*t/T'),
}


@lru_cache(maxsize=32)
def expression(text):
    if not isinstance(text, str) or not 1 <= len(text) <= 1000:
        raise ValueError('函数需为 1–1000 个字符')
    try:
        tree = ast.parse(text, mode='eval')
    except (SyntaxError, RecursionError) as exc:
        raise ValueError('函数语法错误') from exc
    nodes = list(ast.walk(tree))
    if len(nodes) > 200:
        raise ValueError('函数过于复杂')
    allowed = (ast.Expression, ast.BinOp, ast.UnaryOp, ast.Call, ast.Name, ast.Load,
               ast.Constant, ast.Add, ast.Sub, ast.Mult, ast.Div, ast.Pow, ast.Mod,
               ast.USub, ast.UAdd, ast.Compare, ast.Lt, ast.LtE, ast.Gt, ast.GtE, ast.Eq, ast.NotEq)
    for node in nodes:
        if not isinstance(node, allowed):
            raise ValueError('仅支持数学表达式；分段请使用 where(条件, 真值, 假值)')
        if isinstance(node, ast.Name) and node.id not in {*FUNCTIONS, 't', 'T', 'I', 'A', 'pi', 'e'}:
            raise ValueError(f'未知变量或函数：{node.id}')
        if isinstance(node, ast.Call) and (not isinstance(node.func, ast.Name) or node.func.id not in FUNCTIONS or node.keywords):
            raise ValueError('不支持此函数调用')
        if isinstance(node, ast.Constant) and (type(node.value) not in (int, float) or abs(node.value) > 1e12 or not math.isfinite(node.value)):
            raise ValueError('常量必须是绝对值不超过 1e12 的有限数值')
        if isinstance(node, ast.Compare) and len(node.ops) != 1:
            raise ValueError('不支持连写比较，请使用嵌套 where')
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Pow):
            if not isinstance(node.right, ast.Constant) or type(node.right.value) not in (int,float) or not 0 <= node.right.value <= 16:
                raise ValueError('幂指数仅支持 0–16 的常量；负指数请使用除法')
    return compile(tree, '<waveform>', 'eval')


def shape_key(p, sample_hz=HZ):
    validate_sample_hz(sample_hz)
    return (p.mode, p.amplitude, p.function, float(p.function_ms), float(p.rise_ms), float(p.hold_ms), float(p.fall_ms), p.initial, p.dac_bits,
            p.function_layout,p.rise_function,p.fall_function,sample_hz)


def stage_counts(p, sample_hz=HZ):
    validate_sample_hz(sample_hz)
    return tuple(math.floor(d*sample_hz/1000+.5) for d in (p.rise_ms,p.hold_ms,p.fall_ms))


def evaluate(text,t,duration,initial,amplitude):
    with np.errstate(all='ignore'):
        try:
            return np.broadcast_to(np.asarray(eval(expression(text), {'__builtins__': {}},
                dict(FUNCTIONS,t=t,T=duration,I=initial,A=amplitude,pi=np.pi,e=np.e)),dtype=np.float64),t.shape)
        except Exception as exc:
            raise ValueError(f'函数无法计算：{exc}') from exc


def validate_shape(p, sample_hz=HZ):
    validate_sample_hz(sample_hz)
    if type(p.dac_bits) is not int or p.dac_bits not in (10,12,14):
        raise ValueError('DAC 位数须为 10、12 或 14')
    limit=(1 << p.dac_bits)-1
    if p.mode not in ('ramp','function'):
        raise ValueError('未知波形类型')
    if type(p.initial) is not int or not 0 <= p.initial <= limit:
        raise ValueError(f'初始码必须为 0–{limit} 的整数')
    if type(p.amplitude) is not int or not 0 <= p.amplitude <= limit:
        raise ValueError(f'目标幅度码必须为 0–{limit} 的整数')
    if p.function_layout not in ('single','staged'):
        raise ValueError('不支持的函数结构')
    staged=p.mode=='function' and p.function_layout=='staged'
    durations = [p.function_ms] if p.mode == 'function' and not staged else [p.rise_ms,p.hold_ms,p.fall_ms]
    for d in durations:
        if type(d) not in (int,float) or not math.isfinite(d) or not 0 <= d <= 1000:
            raise ValueError('各时长必须为 0～1000 ms 的有限数')
    if p.mode == 'function' and not staged: expression(p.function)
    n = math.floor(sum(durations) * sample_hz / 1000 + .5)
    if staged:
        counts=stage_counts(p,sample_hz); n=sum(counts)
        for d,count in zip(durations,counts):
            if d>0 and count==0: raise ValueError(f'非零阶段时长过短，量化后不足一个 {sample_period_ns(sample_hz):g} ns 采样周期')
        for title,text,count,start,end in (
            ('进入段',p.rise_function,counts[0],p.initial,p.amplitude),
            ('返回段',p.fall_function,counts[2],p.amplitude,p.initial)):
            expression(text)
            if count:
                endpoints=evaluate(text,np.array([0.,count/sample_hz]),count/sample_hz,p.initial,p.amplitude)
                if not np.all(np.isfinite(endpoints)) or not np.allclose(endpoints,[start,end],rtol=0,atol=1e-7):
                    raise ValueError(f'{title}端点必须从 {start} 到 {end}；t=0 为本段起点，t=T 为本段终点')
    if not 1 <= n <= min(MAX_SAMPLES,sample_hz):
        raise ValueError(f'单次总时长须为 {sample_period_ns(sample_hz):g} ns 至 1 s')
    return n


def values(key, ticks, n):
    mode, amplitude, text, duration, rise, hold, fall, initial, bits, layout, rise_text, fall_text = key[:12]
    sample_hz=key[12] if len(key)>12 else HZ
    limit=(1 << bits)-1
    t = ticks / sample_hz
    if mode == 'ramp':
        up, flat, down = rise/1000, hold/1000, fall/1000
        envelope = np.where(t < up, t / max(up, 1/sample_hz),
                     np.where(t < up+flat, 1., np.maximum(0., 1-(t-up-flat)/max(down, 1/sample_hz))))
        y = initial + (amplitude-initial)*envelope
    elif layout=='staged':
        up,flat,down=(math.floor(d*sample_hz/1000+.5) for d in (rise,hold,fall))
        y=np.full(ticks.shape,float(amplitude))
        for mask,start,count,expr in ((ticks<up,0,up,rise_text),(ticks>=up+flat,up+flat,down,fall_text)):
            if count and np.any(mask):
                y[mask]=evaluate(expr,(ticks[mask]-start)/sample_hz,count/sample_hz,initial,amplitude)
    else:
        y=evaluate(text,t,n/sample_hz,initial,amplitude)
    try:
        y = np.broadcast_to(np.asarray(y, dtype=np.float64), ticks.shape)
    except (TypeError,ValueError) as exc:
        raise ValueError('函数必须返回实数幅度') from exc
    if not np.all(np.isfinite(y)):
        raise ValueError('函数在采样点产生 NaN 或无穷大，请检查定义域')
    if np.any(y < -1e-12) or np.any(y > limit+1e-12):
        raise ValueError(f'函数必须返回 0–{limit} 的 DAC 原码；如需限幅请显式使用 clip(f(t),0,{limit})')
    raw = np.clip(y,0,limit)
    return raw, np.floor(raw+.5).astype(np.uint16)


@dataclass(frozen=True)
class Samples:
    runs: tuple  # (held code or interpolation target, core clock count)
    total: int
    max_error: float
    interpolation: bool = False
    error_limit: int | None = None
    checked_samples: int = 0


def compiler_capacity(max_runs=None):
    """Keep legacy full-count diagnostics, extend only for an explicit device limit."""
    if max_runs is None:
        return MAX_RUNS
    if type(max_runs) is not int or not 1 <= max_runs <= PROTOCOL_MAX_RUNS:
        raise ValueError('设备码段容量必须为 1～65535 的整数')
    return max(MAX_RUNS, max_runs)


@lru_cache(maxsize=4)
def _compile(key, n, capacity=MAX_RUNS):
    return compile_range(key,n,0,n,max_runs=capacity)


def compile_range(key,n,offset,sample_count,*,max_runs=MAX_RUNS):
    runs = []
    max_error = 0.
    for begin in range(offset,offset+sample_count,262144):
        check_cancelled()
        ticks = np.arange(begin,min(offset+sample_count,begin+262144),dtype=np.float64)
        raw, codes = values(key,ticks,n)
        max_error = max(max_error,float(np.max(np.abs(raw-codes))))
        boundaries = np.r_[0, np.flatnonzero(codes[1:] != codes[:-1])+1, len(codes)]
        for start, end in zip(boundaries[:-1],boundaries[1:]):
            value, count = int(codes[start]), int(end-start)
            if runs and runs[-1][0] == value:
                runs[-1] = (value,runs[-1][1]+count)
            else:
                runs.append((value,count))
                if len(runs)>max_runs:
                    raise ValueError(f'波形超过软件编译器的 {max_runs} 个连续码段上限；完整码段数尚未统计，请减少波形变化次数。不会自动降采样。')
    return Samples(tuple(runs),sample_count,max_error)


def compile_samples(p, *, max_runs=None, min_ticks=32, sample_hz=HZ):
    validate_sample_hz(sample_hz)
    check_cancelled()
    if p.mode=='scan':
        from .scan import compile_scan
        return compile_scan(p)
    if p.mode=="sequence":
        from .sequences import compile_sequence
        return compile_sequence(p,max_runs=max_runs,min_ticks=min_ticks,sample_hz=sample_hz)
    if p.timing_mode=='gate':
        from .gating import compile_gate
        return compile_gate(p,max_runs=max_runs,min_ticks=min_ticks,sample_hz=sample_hz)
    if p.playback == 'linear':
        from .interpolation import compile_linear, DEFAULT_MAX_RUNS
        return compile_linear(shape_key(p,sample_hz),validate_shape(p,sample_hz),p.interpolation_error,
                              DEFAULT_MAX_RUNS if max_runs is None else max_runs,min_ticks)
    if p.playback != 'hold':raise ValueError('不支持的播放模式')
    return _compile(shape_key(p,sample_hz),validate_shape(p,sample_hz),compiler_capacity(max_runs))


def preview_samples(p, sample_hz=HZ):
    validate_sample_hz(sample_hz)
    if p.mode=="sequence":
        from .sequences import preview_sequence
        return preview_sequence(p,sample_hz)
    if p.timing_mode=='gate':
        from .gating import preview_profile
        p=preview_profile(p,sample_hz)
    n=validate_shape(p,sample_hz)
    ticks=np.unique(np.linspace(0,n-1,min(n,1600)).astype(np.int64))
    if p.mode=='function' and p.function_layout=='staged':
        up,flat,_=stage_counts(p,sample_hz)
        edges=np.array([up-1,up,up+flat-1,up+flat])
        ticks=np.unique(np.r_[ticks,edges[(edges>=0)&(edges<n)]])
    raw,codes=values(shape_key(p,sample_hz),ticks,n)
    return ticks*1000/sample_hz,raw,codes,n*1000/sample_hz
