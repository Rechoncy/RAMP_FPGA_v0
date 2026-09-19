"""Compile analytic envelopes to editable piecewise-linear segments."""
import copy
import math
from .model import Segment,compile_profile


def envelope(profile,kind,peak,up_ms,hold_ms,down_ms,pieces=24):
    if kind not in ("linear","smooth","exponential"):
        raise ValueError("不支持的曲线类型")
    if not all(math.isfinite(v) and v>0 for v in (up_ms,hold_ms,down_ms)):
        raise ValueError("三个阶段的时长必须为正数")
    if not 1<=pieces<=31: raise ValueError("分段精度需为 1–31")
    if kind=="linear": pieces=1
    def shape(x):
        if kind=="smooth": return 3*x*x-2*x*x*x
        if kind=="exponential": return math.expm1(4*x)/math.expm1(4)
        return x
    result=copy.deepcopy(profile); result.segments=[]
    for start,end,duration in ((profile.initial,peak,up_ms),(peak,profile.initial,down_ms)):
        if result.segments: result.segments.append(Segment(peak,hold_ms))
        for i in range(1,pieces+1):
            result.segments.append(Segment(round(start+(end-start)*shape(i/pieces)),duration/pieces))
    compile_profile(result)
    return result
