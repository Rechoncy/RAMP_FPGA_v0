"""Bounded-error descriptors checked against every original rounded sample.

A descriptor (E, N) starting at S emits k=0..N-1, then the next descriptor
starts at E.  All interpolation arithmetic is integer, including ties toward
the larger code on falling slopes.  No raw RLE table is constructed here.
"""
from functools import lru_cache

import numpy as np

from .compiler_control import check_cancelled

MIN_TICKS = 32
DEFAULT_MAX_RUNS = 2048
CHUNK = 262144


def interpolate_codes(start, target, count, indices):
    """Exact RTL reference; 14-bit code * 32-bit ticks fits signed int64."""
    k = np.asarray(indices, dtype=np.int64)
    return start + ((target-start)*k + count//2)//count


def validate_options(error, max_runs, min_ticks):
    if type(error) is not int or not 0 <= error <= 16383:
        raise ValueError('插值最大允许原码差异必须为 0～16383 的整数')
    if type(max_runs) is not int or max_runs < 1:
        raise ValueError('插值段容量必须为正整数')
    if type(min_ticks) is not int or min_ticks < MIN_TICKS:
        raise ValueError(f'插值最短段时长不能小于 {MIN_TICKS} 拍（时间随采样率变化）')


def compile_interval(key, total, offset, count, start_code, end_code,
                     error_limit=1, max_runs=DEFAULT_MAX_RUNS, min_ticks=MIN_TICKS):
    """Bisect intervals until every rounded reference sample meets the bound.

    Node times are integer core ticks; interior target codes are sampled at
    those exact nodes.  Phase endpoints are supplied by the caller.  The
    result is never accepted from node-only or preview-only comparisons.
    """
    from .functions import values, Samples
    validate_options(error_limit, max_runs, min_ticks)
    if count < min_ticks:
        raise ValueError(f'插值阶段仅 {count} 拍，短于最少 {min_ticks} 拍；不会延长波形')
    runs = []
    worst = 0

    @lru_cache(maxsize=None)
    def node(tick):
        return int(values(key, np.array([tick], dtype=np.int64), total)[1][0])

    pending = [(offset, count, start_code, end_code)]
    while pending:
        check_cancelled()
        begin, duration, start, end = pending.pop()
        maximum = 0
        for block in range(0, duration, CHUNK):
            check_cancelled()
            local = np.arange(block, min(duration, block+CHUNK), dtype=np.int64)
            reference = values(key, begin+local, total)[1].astype(np.int64)
            actual = interpolate_codes(start, end, duration, local)
            maximum = max(maximum, int(np.max(np.abs(reference-actual))))
            # Failed candidates will be fully revisited in their children;
            # accepted candidates must pass every block.
            if maximum > error_limit:
                break
        if maximum <= error_limit:
            runs.append((int(end), duration))
            worst = max(worst, maximum)
            continue
        if duration < 2*min_ticks:
            raise ValueError(f'插值精度无法满足：{begin} 拍起的 {duration} 拍短段差异 '
                             f'{maximum} 原码，允许 {error_limit}；最短段为 {min_ticks} 拍，'
                             '当前节点划分无法继续细分，不会放宽精度或延长波形')
        if len(runs)+len(pending)+2 > max_runs:
            raise ValueError(f'按当前自适应节点策略，插值需要超过 {max_runs} 个段槽位才能满足 {error_limit} 原码误差要求；'
                             '超过本通道容量，不会自动放宽精度')
        left = duration//2
        middle = node(begin+left)
        pending.append((begin+left, duration-left, middle, end))
        pending.append((begin, left, start, middle))
    return Samples(tuple(runs), count, worst, True, error_limit, count)


@lru_cache(maxsize=4)
def compile_linear(key, count, error_limit=1, max_runs=DEFAULT_MAX_RUNS, min_ticks=MIN_TICKS):
    """Preserve staged boundaries, including timed plateaus, without RLE."""
    from .functions import values, Samples, HZ
    mode, amplitude, _, _, rise, hold, fall, initial, _, layout, *_ = key
    sample_hz=key[12] if len(key)>12 else HZ
    validate_options(error_limit, max_runs, min_ticks)
    if mode == 'ramp' or layout == 'staged':
        counts = tuple(int(np.floor(d*sample_hz/1000+.5)) for d in (rise,hold,fall))
        # A legacy ramp uses a total-time rounding rule. Its raw sampler is
        # still the source of truth, so retain the exact total count.
        counts = (counts[0], counts[1], count-counts[0]-counts[1])
        boundaries = [(counts[0], initial, amplitude),
                      (counts[1], amplitude, amplitude),
                      (counts[2], amplitude, initial)]
    else:
        endpoint = int(values(key, np.array([count], dtype=np.int64), count)[1][0])
        boundaries = [(count, initial, endpoint)]
    runs = []; offset = 0; worst = 0; previous = initial
    for duration, start, end in boundaries:
        if not duration:
            # A zero-duration phase may imply an instantaneous jump. Such a
            # jump cannot be hidden inside a continuous linear descriptor.
            continue
        data = compile_interval(key, count, offset, duration, previous, end,
                                error_limit, max_runs-len(runs), min_ticks)
        runs.extend(data.runs); worst = max(worst, data.max_error)
        offset += duration; previous = end
    return Samples(tuple(runs), count, worst, True, error_limit, count)
