"""Event-program demo. Timing is illustrative; RTL tests verify individual clocks."""
from . import simulator as timing


class ProgramPlayback:
    def __init__(self,owner):self.owner=owner;self.index=0;self.phase=0;self.runner=None;self.completed=0

    def load(self):
        o=self.owner;a=o.active;s=a['steps'][self.index]
        _,kind,source,edge,base,count,rise,target,fall,delay,repeat,flags=s
        self.step=s;self.runner=timing.Simulator(1,o.trigger_count,o.dac_bits,core_hz=o.core_hz)
        self.runner.active=dict(initial=a['initial'],safe=a['safe'],id=a['id'],count=count,segments=a['segments'][base:base+count],
                                samples=True,divider=1,repeat=repeat,external=kind==1,channel=0,trigger_input=source,
                                trigger_edge='falling' if edge else 'rising',gate=kind==1,rise_runs=rise,target=target,fall_ticks=fall,linear=bool(flags&1))
        self.runner.state=1;self.runner.code=a['initial'];self.ready_at=timing.time.monotonic()+20/o.core_hz
        self.delay_at=self.ready_at+delay/o.core_hz;self.phase=0;o.state=1;o.code=a['initial'];o.seg=base

    def arm(self):self.index=0;self.completed=0;self.owner.ignored=0;self.load()

    def refresh(self):
        o=self.owner
        if o.state==0 or self.runner is None:return
        now=timing.time.monotonic();kind=self.step[1]
        if self.phase==0:
            if now<self.ready_at:return
            self.phase=2 if kind==2 else 1;o.state=2 if kind==2 else 1
        if self.phase==2 and now>=self.delay_at:self.runner.start();self.phase=3;o.state=2
        if self.phase==3:
            self.runner.refresh();o.code=self.runner.code;o.phase=self.runner.phase;o.seg=self.step[4]+self.runner.seg
            if self.runner.state!=2:
                self.index+=1
                if self.index==len(o.active['steps']):self.index=0;self.completed=min(65535,self.completed+1)
                self.load()
        o.loops=self.completed

    def external(self,index,edge):
        self.refresh();o=self.owner
        if o.state==0:return
        _,kind,source,falling,*_=self.step
        if index is not None and index!=source:return
        if self.phase==3 and kind==1:
            self.runner.external_trigger(index,edge);self.refresh();return
        expected='falling' if falling else 'rising'
        if edge is not None and edge!=expected:return
        if self.phase==1:
            self.runner.start();self.phase=3;o.state=2;self.refresh()
        elif kind!=2:o.ignored=min(0xffffffff,o.ignored+1)
