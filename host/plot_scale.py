"""Integer DAC axes for bounded preview data; never modifies output samples."""
from dataclasses import dataclass
import math


@dataclass(frozen=True)
class CodeAxis:
    minimum: int
    maximum: int
    ticks: tuple[int, ...]

    @property
    def span(self):
        return self.maximum-self.minimum


def code_axis(values, bits):
    """Fit the visible points, with readable ticks and legal-DAC bounds."""
    limit=(1<<bits)-1
    low=math.inf;high=-math.inf
    for value in values:
        value=float(value)
        if math.isfinite(value):low=min(low,value);high=max(high,value)
    if not math.isfinite(low):low,high=0,limit
    low=max(0,min(limit,low));high=max(low,min(limit,high))
    # A flat line or a one-code change still needs a useful vertical span.
    center=(low+high)/2;half=max(2,(high-low)/2)
    padding=max(1,(high-low)*.08)
    lower=max(0,center-half-padding);upper=min(limit,center+half+padding)
    target=max(1,(upper-lower)/5)
    power=10**math.floor(math.log10(target))
    step=next(int(math.ceil(power*factor)) for factor in (1,2,2.5,5,10) if power*factor>=target)
    minimum=max(0,math.floor(lower/step)*step)
    maximum=min(limit,math.ceil(upper/step)*step)
    ticks=list(range(minimum,maximum+1,step))
    if ticks[-1]!=maximum:
        # Do not crowd a near-full-scale round tick against the DAC limit.
        if len(ticks)>1 and maximum-ticks[-1]<step*.4:ticks.pop()
        ticks.append(maximum)
    return CodeAxis(minimum,maximum,tuple(ticks))
