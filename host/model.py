"""Physical-unit profiles and the firmware v1 waveform compiler."""
from __future__ import annotations

from dataclasses import dataclass, asdict, field
import json
import math
import uuid
from .sample_rates import supports_sample_clock

CORE_HZ = 200_000_000
MAX_SEGMENTS = 64
MIN_DIVIDER = 16  # 80 ns; waveform pipeline limit, independent of DAC clock


@dataclass
class Segment:
    target: int = 800
    duration_ms: float = 20.0


@dataclass
class Profile:
    name: str = "基础 Ramp"
    initial: int = 100
    safe: int = 100
    update_us: float = 1.0
    repeat: int = 1  # 0 means infinite fixed playback (Ramp, function, or timeline).
    trigger: str = "external"
    fm_initial: int = 0
    fm_step: int = 1
    fm_points: int = 1024
    segments: list[Segment] = field(default_factory=lambda: [
        Segment(800, 20), Segment(800, 100), Segment(100, 50)])
    id: str = field(default_factory=lambda: uuid.uuid4().hex)
    schema: int = 1
    mode: str = "segments"  # retain imported v1 profiles without changing their output
    dac_bits: int = 10
    amplitude: int = 112
    rise_ms: float = 20.
    hold_ms: float = 100.
    fall_ms: float = 50.
    function: str = "I - 400 * sin(2*pi*t/T)"
    function_ms: float = 0.01
    function_layout: str = "staged"
    rise_function: str = "I + (A-I)*(exp(-4.5*(1-t/T)**2)-exp(-4.5))/(1-exp(-4.5))"
    fall_function: str = "I + (A-I)*(exp(-4.5*(t/T)**2)-exp(-4.5))/(1-exp(-4.5))"
    channel: int = 0
    trigger_input: int = 0
    trigger_edge: str = "rising"
    timing_mode: str = "fixed"
    sequence_mode: str = "timeline"
    sequence_steps: list[dict] = field(default_factory=list)
    scan_start: int = 0
    scan_step: int = 1
    scan_points: int = 1024
    scan_end: str = 'hold'
    playback: str = 'hold'
    interpolation_error: int = 1

    def to_dict(self):
        data=asdict(self)
        if self.playback == 'hold' and self.interpolation_error == 1:
            data.pop('playback'); data.pop('interpolation_error')
        if self.mode!="sequence":
            data.pop("sequence_mode");data.pop("sequence_steps")
        if self.mode!='scan':
            for key in ('scan_start','scan_step','scan_points','scan_end'):data.pop(key)
        return data

    @classmethod
    def from_dict(cls, data):
        if data.get("schema") != 1:
            raise ValueError("不支持的方案版本")
        values = {k: data[k] for k in ("name", "initial", "safe", "update_us", "repeat", "trigger", "id", "schema")}
        values["segments"] = [Segment(**s) for s in data["segments"]]
        values.update({k: data.get(k, default) for k, default in (("fm_initial", 0), ("fm_step", 1), ("fm_points", 1024))})
        values.update({k:data[k] for k in ('mode','amplitude','rise_ms','hold_ms','fall_ms','function','function_ms','channel','dac_bits') if k in data})
        # Existing whole-wave expressions keep exactly their original sampling semantics.
        values['function_layout']=data.get('function_layout','single')
        values.update({k:data[k] for k in ('rise_function','fall_function') if k in data})
        values['trigger_input']=data.get('trigger_input',data.get('channel',0))
        values['trigger_edge']=data.get('trigger_edge','rising')
        values['timing_mode']=data.get('timing_mode','fixed')
        values['sequence_mode']=data.get('sequence_mode','timeline')
        values['sequence_steps']=data.get('sequence_steps',[])
        values.update({k:data[k] for k in ('scan_start','scan_step','scan_points','scan_end') if k in data})
        values.update({k:data[k] for k in ('playback','interpolation_error') if k in data})
        p = cls(**values)
        compile_profile(p,offline_capabilities(dac_bits=p.dac_bits,sequence_steps=64,infinite_repeat=True,trigger_scan=True))
        return p


@dataclass(frozen=True)
class Capabilities:
    core_hz: int = CORE_HZ
    min_divider: int = MIN_DIVIDER
    max_segments: int = MAX_SEGMENTS
    dac_bits: int = 10
    dac_divider: int = 1
    board_id: int = 1
    max_runs: int = 8192
    dac_count: int = 8  # offline compiler ceiling; hardware advertises actual counts
    trigger_count: int = 8
    trigger_routing: bool = True
    gate_mode: bool = True
    parallel_channels: bool = False
    fixed_auto_rearm: bool = False
    boot_profiles: bool = False
    sequence_steps: int = 0
    infinite_repeat: bool = False
    trigger_scan: bool = False
    interpolation: bool = False
    interpolation_min_ticks: int = 32
    run_batch: bool = False
    usb_fifo: bool = False
    max_batch_runs: int = 1
    uart_autobaud: bool = False
    trigger_debug: bool = False


def offline_capabilities(**kwargs):
    """Compiler capability ceiling, never evidence of connected hardware support."""
    kwargs.setdefault('interpolation', True)
    return Capabilities(**kwargs)


@dataclass(frozen=True)
class Compiled:
    divider: int
    counts: tuple[int, ...]
    duration_ms: float
    requested_ms: float


def integer(value, label, lower, upper):
    if isinstance(value, bool) or not isinstance(value, int) or not lower <= value <= upper:
        raise ValueError(f"{label}必须为 {lower}–{upper} 的整数")


def compile_profile(profile: Profile, caps=Capabilities()) -> Compiled:
    if profile.playback not in ('hold', 'linear'):
        raise ValueError('不支持的播放模式')
    integer(profile.interpolation_error, '插值最大允许原码差异', 0, 16383)
    if profile.playback == 'linear':
        if profile.mode not in ('ramp', 'function', 'sequence'):
            raise ValueError('分段线性插值仅支持 Ramp、函数及执行序列')
        if not caps.interpolation:
            raise ValueError('当前固件不支持分段线性插值，请生成并烧录支持插值的新固件')
    if caps.parallel_channels and profile.mode not in ('ramp','function','sequence','scan'):
        raise ValueError('独立多路固件请使用 Ramp 或自定义函数；不支持旧版分段/FM')
    if not isinstance(profile.name, str) or not profile.name.strip() or len(profile.name) > 100:
        raise ValueError("方案名称需为 1–100 个字符")
    if type(profile.dac_bits) is not int or profile.dac_bits not in (10,12,14):
        raise ValueError("DAC 位数须为 10、12 或 14")
    if profile.dac_bits != caps.dac_bits:
        raise ValueError(f"方案为 {profile.dac_bits} 位，设备为 {caps.dac_bits} 位；请在对应硬件配置下编辑并确认原码")
    limit = (1 << caps.dac_bits) - 1
    integer(profile.initial, "初始码", 0, limit)
    integer(profile.safe, "中止码", 0, limit)
    infinite_allowed = ((profile.mode == 'sequence' and profile.sequence_mode == 'timeline')
                        or (profile.mode in ('ramp', 'function') and profile.timing_mode == 'fixed'))
    integer(profile.repeat, "重复次数", 0 if infinite_allowed else 1, 65535)
    if profile.repeat == 0 and not caps.infinite_repeat:
        raise ValueError('无限重复需要新版 FPGA 固件，请重新生成并烧录；也可改用指定次数')
    if profile.trigger not in ("external", "software"):
        raise ValueError("不支持的触发模式")
    if profile.mode=='scan':
        from .scan import validate_scan
        validate_scan(profile,caps)
        return Compiled(1,(),0,0)
    if profile.mode=='sequence':
        from .sequences import validate_sequence
        total=validate_sequence(profile,caps)
        return Compiled(1,(),total*1000/caps.core_hz,total*1000/caps.core_hz)
    if profile.timing_mode not in ('fixed','gate'):raise ValueError('不支持的时间控制模式')
    if profile.timing_mode=='gate' and profile.mode=='segments':raise ValueError('电平门控不支持旧版分段模式')
    if profile.mode in ('ramp','function'):
        from .functions import validate_shape, HZ
        if profile.timing_mode=='gate':
            if not caps.gate_mode:raise ValueError('当前固件不支持电平门控，请重新生成并烧录新版固件')
            from .gating import validate_gate
            n=validate_gate(profile,caps.core_hz)
        else:n=validate_shape(profile,caps.core_hz)
        integer(profile.channel, '输出 DAC 编号（从 0 开始）', 0, caps.dac_count-1)
        integer(profile.trigger_input, '触发输入编号（从 0 开始）', 0, 7)
        if profile.trigger_edge not in ('rising','falling'): raise ValueError('不支持的触发边沿')
        if profile.trigger=='external':
            integer(profile.trigger_input, '当前硬件触发输入编号（从 0 开始）', 0, caps.trigger_count-1)
            if not caps.trigger_routing and (profile.trigger_input!=profile.channel or profile.trigger_edge!='rising'):
                raise ValueError('旧固件仅支持同编号触发输入的上升沿；自定义触发映射请更新固件')
        if not supports_sample_clock(caps):
            raise ValueError('当前固件不支持所需采样时钟的函数播放，请生成并烧录匹配采样率的新版固件')
        requested=profile.rise_ms+profile.fall_ms if profile.timing_mode=='gate' else profile.function_ms if profile.mode=='function' and profile.function_layout=='single' else profile.rise_ms+profile.hold_ms+profile.fall_ms
        return Compiled(1,(),n*1000/caps.core_hz,requested)
    if caps.dac_bits != 10: raise ValueError('旧分段/FM 模式仅适用于 10 位；请使用单幅度 Ramp 或自定义函数')
    if profile.mode != 'segments': raise ValueError('不支持的波形类型')
    # These fields belong exclusively to the retired dual-output protocol.
    # Imported metadata must not affect modern Ramp/function/sequence validity.
    integer(profile.fm_initial, "FM 初始码", 0, limit)
    integer(profile.fm_step, "FM 步长", 0, limit)
    integer(profile.fm_points, "FM 点数", 1, 1024)
    if profile.fm_initial + profile.fm_step * (profile.fm_points - 1) > limit:
        raise ValueError("FM 扫描终点超出 DAC 范围")
    if not 1 <= len(profile.segments) <= caps.max_segments:
        raise ValueError(f"段数需为 1–{caps.max_segments}")
    if profile.repeat > 1 and profile.segments[-1].target != profile.initial:
        raise ValueError("重复运行时末段目标必须等于初始码，以保持循环边界连续")
    if not isinstance(profile.update_us, (int, float)) or not math.isfinite(profile.update_us) or profile.update_us <= 0:
        raise ValueError("更新间隔必须为正数")
    divider = round(profile.update_us * caps.core_hz / 1e6 / caps.dac_divider) * caps.dac_divider
    if not caps.min_divider <= divider <= 65535:
        raise ValueError(f"更新间隔需对应 {caps.min_divider}–65535 个核心周期")
    counts = []
    for i, s in enumerate(profile.segments):
        integer(s.target, f"第 {i + 1} 段目标码", 0, limit)
        if not isinstance(s.duration_ms, (int, float)) or not math.isfinite(s.duration_ms) or s.duration_ms <= 0:
            raise ValueError(f"第 {i + 1} 段时长必须为正数")
        ticks = math.floor(s.duration_ms * caps.core_hz / (1000 * divider) + 0.5)
        if not 1 <= ticks <= 0xFFFFFFFF:
            raise ValueError(f"第 {i + 1} 段时长超出硬件范围")
        counts.append(ticks)
    return Compiled(divider, tuple(counts), sum(counts) * divider * 1000 / caps.core_hz,
                    sum(s.duration_ms for s in profile.segments))


def fingerprint(profile):
    data=profile.to_dict()
    for key in ("update_us","rise_ms","hold_ms","fall_ms","function_ms"):data[key]=float(data[key])
    for s in data["segments"]: s["duration_ms"]=float(s["duration_ms"])
    return json.dumps(data, sort_keys=True, ensure_ascii=False, allow_nan=False)


def presets():
    return [Profile(name="单幅度 Ramp", mode="ramp", initial=512, safe=512),
            Profile(name="三段高斯", mode="function", initial=512, safe=512)]
