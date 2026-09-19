"""Read-only assessment of the storage advertised by the loaded firmware.

UART INFO reports usable per-channel descriptor counts, not the FPGA's total
LUT/BRAM inventory. Sample counts are not RAM usage: equal consecutive DAC codes
are stored as one run, and native trigger scans need no waveform descriptors.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass

from .model import Capabilities, compile_profile, fingerprint
from .compiler_control import CompilationCancelled


@dataclass(frozen=True)
class ResourceUsage:
    channel: int
    name: str
    profile_fingerprint: str
    mode: str
    capabilities: Capabilities
    fits: bool
    error: str
    required_runs: int | None
    max_runs: int
    required_steps: int
    max_steps: int
    samples: int | None
    scan_points: int
    required_segments: int
    max_segments: int
    error_kind: str = ''
    interpolation: bool = False
    error_limit: int | None = None
    max_error: int | None = None
    checked_samples: int = 0

    def summary(self):
        if self.mode == 'scan':
            details = f'触发步进 {self.scan_points:,} 点 · 不占波形码段'
        elif self.mode == 'segments':
            details = f'旧版分段 {self.required_segments:,} / {self.max_segments:,} 段'
        else:
            used = '未完成计数' if self.required_runs is None else f'{self.required_runs:,}'
            kind='插值/保持波形池' if self.interpolation and self.required_steps else '插值段' if self.interpolation else '连续码段'
            details = f'{kind} {used} / {self.max_runs:,}'
            if self.required_steps:
                details += f' · 事件步骤 {self.required_steps:,} / {self.max_steps:,}'
            if self.interpolation:
                details += f' · 允许差异 {self.error_limit} 原码'
                if self.max_error is not None:
                    details += f' · 完整检查 {self.checked_samples:,} 点，最大差异 {self.max_error} 原码'
        return details + (f' · {self.error}' if self.error else ' · 容量通过')

    def report(self):
        """JSON-compatible report for logs; no compiled sample data is retained."""
        return asdict(self)


def assess_resources(profile, caps: Capabilities) -> ResourceUsage:
    """Compile exactly once (compiler cache applies) without changing the device.

    On compiler failure the complete number of runs is unknown. In particular,
    an early stop at the compiler's 8192-run ceiling is never reported as an
    exact waveform size, nor confused with a capacity read from the device.
    """
    steps = len(profile.sequence_steps) if profile.mode == 'sequence' and profile.sequence_mode == 'events' and isinstance(profile.sequence_steps,list) else 0
    segments = len(profile.segments) if profile.mode == 'segments' and isinstance(profile.segments,list) else 0
    scan_points = profile.scan_points if profile.mode == 'scan' else 0
    from .sequences import has_interpolation
    linear = has_interpolation(profile)
    values = dict(channel=profile.channel, name=profile.name, profile_fingerprint='',
                  mode=profile.mode, capabilities=caps, fits=False, error='',
                  required_runs=None, max_runs=caps.max_runs, required_steps=steps,
                  max_steps=caps.sequence_steps, samples=None, scan_points=scan_points,
                  required_segments=segments, max_segments=caps.max_segments, error_kind='',
                  interpolation=linear,error_limit=profile.interpolation_error if linear else None)
    try:
        values['profile_fingerprint'] = fingerprint(profile)
        compiled = compile_profile(profile, caps)
        if profile.mode == 'segments':
            values.update(required_runs=0, samples=sum(compiled.counts)*compiled.divider)
        else:
            from .functions import compile_samples
            samples = compile_samples(profile,max_runs=caps.max_runs,min_ticks=caps.interpolation_min_ticks,sample_hz=caps.core_hz)
            values.update(required_runs=len(samples.runs), samples=samples.total)
            if linear:
                values.update(error_limit=samples.error_limit,max_error=int(samples.max_error),checked_samples=samples.checked_samples)
        errors = []
        if values['required_runs'] > caps.max_runs:
            errors.append(f'需要 {values["required_runs"]:,} 个连续码段，超过设备每路 {caps.max_runs:,} 个容量')
        if steps > caps.sequence_steps:
            errors.append(f'需要 {steps} 个事件步骤，超过设备每路 {caps.sequence_steps} 个容量')
        if errors:
            values.update(error='；'.join(errors), error_kind='capacity')
        else:
            values['fits'] = True
    except Exception as exc:
        if isinstance(exc,CompilationCancelled):raise
        error = str(exc)
        if steps > caps.sequence_steps > 0 and '步骤' in error and '容量' in error:
            values.update(error=f'需要 {steps} 个事件步骤，超过设备每路 {caps.sequence_steps} 个容量', error_kind='capacity')
        elif segments > caps.max_segments and '段数' in error:
            values.update(error=f'需要 {segments} 个旧版分段，超过设备 {caps.max_segments} 段容量', error_kind='capacity')
        elif linear and ('容量' in error or '段槽位' in error):
            values.update(error=error,error_kind='capacity')
        elif '8192' in error and ('码段' in error or '连续码段' in error):
            values.update(error='达到软件编译器的 8192 码段上限，完整码段数尚未统计；无法下发，请减少波形变化次数。不会自动降采样。', error_kind='compiler_limit')
        else:
            values.update(error=error, error_kind='invalid')
    return ResourceUsage(**values)


def require_resources(reports):
    """Reject the entire selection before a BEGIN/COMMIT changes any waveform."""
    failures = [f'DAC {r.channel + 1}：{r.error}' for r in reports if not r.fits]
    if failures:
        raise ValueError('资源检查未通过：' + '；'.join(failures))


def preflight_resources(device, profiles, progress=lambda text: None,
                        resources=lambda reports: None, capabilities=lambda caps: None):
    """Re-read INFO, assess every selected profile, publish results, then reject."""
    caps = device.refresh_capabilities()
    capabilities(caps)
    device.check_output_clock()
    reports = []
    for profile in profiles:
        progress(f'完整检查 DAC {profile.channel + 1}：{profile.name}')
        reports.append(assess_resources(profile, caps))
    reports = tuple(reports)
    resources(reports)
    require_resources(reports)
    return reports
