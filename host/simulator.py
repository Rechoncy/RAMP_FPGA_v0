"""Protocol-compatible demo device. No serial port is opened in demo mode."""
import struct
import time
from .protocol import *


class Simulator:
    def __init__(self, dac_count=2, trigger_count=2, dac_bits=10, interpolation=True, core_hz=200_000_000):
        from .sample_rates import validate_sample_hz
        self.core_hz=validate_sample_hz(core_hz)
        if dac_bits not in (10,12,14): raise ValueError("Unsupported DAC width")
        self.dac_bits=dac_bits; self.limit=(1 << dac_bits)-1; self.mid=1 << (dac_bits-1)
        self.dac_count=dac_count; self.trigger_count=trigger_count
        self.state = 0
        self.code = self.mid
        self.seg = 0
        self.loops = 0
        self.ignored = 0
        self.active = None
        self.pending = None
        self.cache = None
        self.started = 0
        self.phase = 0
        self.interpolation=interpolation

    def close(self):
        pass

    def external_trigger(self, index=None, edge=None):
        if self.active and self.active.get("program"):
            self.program.external(index,edge);return
        if self.active and self.active.get('gate'):
            self.refresh()
            if index is not None and index!=self.active['trigger_input']:return
            if edge=='falling' and self.state==2 and self.phase in (1,2):
                self.captured=self.code
                self.phase=4 if self.phase==1 else 3
                self.started=time.monotonic()
            elif edge in (None,'rising'):self.start()
            return
        if self.active and self.active["external"] and (index is None or index==self.active.get('trigger_input',0)) and (edge is None or edge==self.active.get('trigger_edge','rising')):
            self.start()

    def start(self):
        self.refresh()
        if self.active and self.active.get('scan'):
            if self.state==1:
                a=self.active;index=self.seg if self.seg<a['points'] else 0
                self.code=a['start']+index*a['step'];self.seg=index+1
                if self.seg==a['points']:
                    self.loops=min(65535,self.loops+1)
                    if not a['loop']:self.state=3
            elif self.state==3:self.ignored=min(0xffffffff,self.ignored+1)
            return
        if self.state == 1:
            self.state = 2
            self.round_base=self.loops if self.active.get('samples') and self.active['external'] else 0
            self.started = time.monotonic()
            self.phase=1 if self.active.get('gate') else 0
        elif self.state == 2:
            self.ignored = (self.ignored + 1) & 0xFFFFFFFF

    def refresh(self):
        if self.active and self.active.get("program"):
            self.program.refresh();return
        if self.state != 2:
            return
        a = self.active
        if a.get('gate'):
            self.refresh_gate()
            return
        total = sum(n for _, n in a["segments"]) * a["divider"] / self.core_hz
        elapsed = time.monotonic() - self.started
        completed = int(elapsed / total)
        self.loops = min(65535,self.round_base + (min(completed, a["repeat"]) if a["repeat"] else completed))
        if a["repeat"] and elapsed >= total * a["repeat"]:
            self.state = 1 if a.get('samples') and a['external'] else 3
            self.code = a["initial"] if a.get("samples") else a["segments"][-1][0]
            self.seg = 0 if a.get("samples") else len(a["segments"]) - 1
            return
        ticks = int((elapsed % total) * self.core_hz / a["divider"])
        previous = a["initial"]
        for i, (target, n) in enumerate(a["segments"]):
            if ticks < n:
                self.seg = i
                d = abs(target - previous)
                self.code = (previous+((target-previous)*ticks+n//2)//n if a.get('linear') else
                             target if a.get("samples") else previous + (1 if target >= previous else -1) * (d * ticks // n))
                return
            ticks -= n
            previous = target

    def refresh_gate(self):
        a=self.active; ticks=int((time.monotonic()-self.started)*self.core_hz)
        boundary=a['rise_runs']
        if self.phase==2:return
        if self.phase in (4,5):
            ticks-=self.dac_bits+1
            if ticks<0:return
            self.phase=5
            delta=abs(a['initial']-self.captured)*min(ticks,a['fall_ticks'])//a['fall_ticks']
            self.code=self.captured+(1 if a['initial']>=self.captured else -1)*delta
            if ticks<a['fall_ticks']:return
        else:
            indices=range(boundary) if self.phase==1 else range(boundary,len(a['segments']))
            previous=a['initial'] if self.phase==1 else a['target']
            for i in indices:
                code,n=a['segments'][i]
                if ticks<n:
                    self.code=previous+((code-previous)*ticks+n//2)//n if a.get('linear') else code
                    self.seg=i; return
                ticks-=n
                previous=code
            if self.phase==1:self.phase=2; self.code=a['target']; return
        self.phase=0; self.state=1; self.code=a['initial']; self.seg=0
        self.loops=min(65535,self.loops+1)

    def exchange(self, frame):
        if self.cache and self.cache[0] == frame:
            return self.cache[1]
        self.refresh()
        cmd, p = frame[3], frame[6:30]
        reply = bytearray(24)
        error = 0
        if cmd == INFO:
            struct.pack_into("<IHHBBH", reply, 1, self.core_hz, 16, 64, self.dac_bits, 1, 1)
            struct.pack_into("<HBBB",reply,13,8192,self.dac_count,self.trigger_count,235)
            reply[18]=64
            if self.interpolation:reply[19:22]=bytes([1,32,1])
        elif cmd == STATUS:
            struct.pack_into("<BBHHII", reply, 1, self.state, self.seg & 255, self.code, self.loops,
                             self.active["id"] if self.active else 0, self.ignored)
            reply[15:17] = bytes([self.active is not None, 1])
            reply[17] = self.active.get('channel',0) if self.active else 0
            struct.pack_into('<H',reply,18,self.seg)
            reply[20] = bool(self.active and self.active.get('samples'))
            if self.active and self.active.get('linear'):reply[20]|=2
            reply[21] = self.active.get('trigger_input',0) if self.active else 0
            reply[22] = self.active.get('trigger_edge')=='falling' if self.active else 0
            reply[23]=(128 | self.phase) if self.active and self.active.get('gate') else 0
            if self.active and self.active.get('samples') and self.active['repeat']==0:reply[23]|=16
            if self.active and self.active.get('program'):
                step=self.program.step;reply[2]=self.program.index;reply[21]=step[2]
                reply[20]=1 | (2 if step[11]&1 else 0)
                reply[22]=step[3] | (self.program.phase<<1) | (128 if self.program.phase==1 and self.state==1 else 0)
                reply[23]=32 | (128 if step[1]==1 else 0) | self.phase
            if self.active and self.active.get('scan'):
                reply[23]=8 | (16 if self.active['loop'] else 0)
                reply[22]|=128 if self.state==1 else 0
            if reply[20]&2 and not self.active.get('program') and self.state==1:reply[22]|=128
        elif cmd in (BEGIN_LINEAR,BEGIN_LINEAR_GATE,BEGIN_SEQUENCE_LINEAR) and not self.interpolation:
            error=1
        elif cmd == BEGIN:
            if self.state in (1, 2):
                error = 2
            else:
                initial, divider, count, repeat, identity, external, safe = struct.unpack_from("<HHHHIBxH", p)
                if self.dac_bits!=10 or initial > 1023 or safe > 1023 or divider < 16 or not 1 <= count <= 64 or not repeat or external > 1:
                    error = 3
                else:
                    self.pending = dict(initial=initial, divider=divider, count=count, repeat=repeat,
                        id=identity, external=external, safe=safe, segments=[])
        elif cmd == BEGIN_SCAN:
            from .scan import SCAN_FORMAT
            initial,safe,start,step,points,identity,channel,source,flags=struct.unpack_from(SCAN_FORMAT,p)
            last=start+(points-1)*step
            if self.state in (1,2):error=2
            elif max(initial,safe,start)>self.limit or not -self.limit<=step<=self.limit or not points or not 0<=last<=self.limit or channel>=self.dac_count or source>7 or flags>7 or (flags&1 and source>=self.trigger_count) or any(p[17:]):error=3
            else:self.pending=dict(initial=initial,safe=safe,start=start,step=step,points=points,id=identity,channel=channel,
                trigger_input=source,trigger_edge='falling' if flags&2 else 'rising',loop=bool(flags&4),external=bool(flags&1),scan=True,samples=True,repeat=1,divider=1,count=0,segments=[])
        elif cmd in (BEGIN_SEQUENCE,BEGIN_SEQUENCE_LINEAR):
            initial,safe,count,steps,identity,channel=struct.unpack_from('<HHHHIB',p)
            if self.state in (1,2):error=2
            elif max(initial,safe)>self.limit or not 1<=count<=8192 or not 1<=steps<=64 or channel>=self.dac_count or any(p[13:]):error=3
            else:self.pending=dict(initial=initial,safe=safe,count=count,step_count=steps,id=identity,channel=channel,external=True,trigger_input=0,trigger_edge='rising',divider=1,samples=True,program=True,program_linear=cmd==BEGIN_SEQUENCE_LINEAR,repeat=1,segments=[],steps=[])
        elif cmd == WRITE_STEP:
            from .sequences import STEP_FORMAT
            step=struct.unpack(STEP_FORMAT,p)
            i,kind,source,edge,base,count,rise,target,fall,delay,repeat,reserved=step
            if self.state in (1,2):error=2
            elif not self.pending or not self.pending.get('program'):error=4
            elif i!=len(self.pending['steps']) or i>=self.pending['step_count'] or kind>2 or (i==0 and kind==2) or source>=self.trigger_count or edge>1 or not count or base+count>self.pending['count'] or target>self.limit or not repeat or reserved>(1 if self.pending.get('program_linear') else 0) or (kind==1 and (not 1<=rise<count or not 1<=fall<=self.core_hz or repeat!=1 or edge)):error=3
            else:self.pending['steps'].append(step)
        elif cmd in (BEGIN_GATE,BEGIN_LINEAR_GATE):
            initial,safe,count,rise_runs,identity,channel,source,target,fall_ticks=struct.unpack_from('<HHHHIBBHI',p)
            if self.state in (1,2):error=2
            elif max(initial,safe,target)>self.limit or not 2<=count<=8192 or not 1<=rise_runs<count or channel>=self.dac_count or source>=self.trigger_count or not 1<=fall_ticks<=self.core_hz:error=3
            else:self.pending=dict(initial=initial,safe=safe,count=count,rise_runs=rise_runs,id=identity,channel=channel,trigger_input=source,target=target,fall_ticks=fall_ticks,external=True,trigger_edge='rising',divider=1,samples=True,linear=cmd==BEGIN_LINEAR_GATE,gate=True,repeat=1,segments=[])
        elif cmd in (BEGIN_RUNS,BEGIN_LINEAR):
            initial,safe,count,repeat,identity,external,channel=struct.unpack_from('<HHHHIBB',p)
            trigger_input=p[14] if p[16]&1 else channel
            trigger_edge='falling' if p[16]&1 and p[15] else 'rising'
            if self.state in (1,2): error=2
            elif initial>self.limit or safe>self.limit or not 1<=count<=8192 or external>1 or channel>=self.dac_count or (external and trigger_input>=self.trigger_count) or p[16]>1 or (p[16] and p[15]>1): error=3
            else:
                self.pending=dict(initial=initial,safe=safe,count=count,repeat=repeat,id=identity,
                    external=external,channel=channel,trigger_input=trigger_input,trigger_edge=trigger_edge,divider=1,samples=True,linear=cmd==BEGIN_LINEAR,segments=[])
        elif cmd in (SEGMENT,RUN):
            index, target, n = struct.unpack_from("<HHI", p)
            if self.state in (1, 2):
                error = 2
            elif self.pending is None or bool(self.pending.get('samples')) != (cmd==RUN):
                error = 4
            elif index != len(self.pending["segments"]) or index >= self.pending["count"] or target > self.limit or not n or self.pending.get('linear') and n<32:
                error = 3
            else:
                self.pending["segments"].append((target, n))
        elif cmd == COMMIT:
            if self.state in (1, 2):
                error = 2
            elif not self.pending or len(self.pending["segments"]) != self.pending["count"] or (self.pending.get("program") and len(self.pending["steps"])!=self.pending["step_count"]):
                error = 4
            elif not self.valid_linear_pending():
                error = 3
            else:
                self.active, self.pending = self.pending, None
                if self.active.get("program"):
                    from .sequence_simulator import ProgramPlayback
                    self.program=ProgramPlayback(self);self.program.arm()
                self.state = 0
                self.code = self.active["initial"]
                self.seg = self.loops = 0
                self.phase=0
        elif cmd == ARM:
            if self.state in (1, 2):
                error = 2
            elif self.active is None:
                error = 4
            else:
                self.state, self.code = 1, self.active["initial"]
                self.seg = self.loops = self.ignored = 0
                self.phase=0
                if self.active.get("program"):self.program.arm()
        elif cmd == TRIGGER:
            if self.state != 1 or self.active["external"]:
                error = 2
            else:
                self.start()
        elif cmd == ABORT:
            if self.active and self.active.get("program"):
                self.program.phase=0;self.program.index=0;self.program.completed=0;self.loops=self.seg=self.ignored=0
            self.state = 0
            self.phase=0
            if self.active and (self.active.get('gate') or self.active.get('scan')):self.seg=self.loops=self.ignored=0
            self.code = self.active["safe"] if self.active else self.mid
        elif cmd == DISARM:
            if self.active and self.active.get("program") and self.state!=2:
                self.program.phase=0;self.program.index=0;self.program.completed=0;self.loops=self.seg=self.ignored=0
            if self.state == 2:
                error = 2
            else:
                self.state = 0
                self.phase=0
                if self.active and (self.active.get('gate') or self.active.get('scan')):self.seg=self.loops=self.ignored=0
                if self.active and self.active.get("samples"):
                    self.code = self.active["initial"]
        else:
            error = 1
        reply[0] = error
        response = encode(cmd | 0x80, struct.unpack_from("<H", frame, 4)[0], reply)
        self.cache = (frame, response)
        return response

    def valid_linear_pending(self):
        a=self.pending
        if not a:return False
        checks=[]
        if a.get('linear'):
            checks.append((a['segments'],a.get('gate',False),a.get('rise_runs',0),a.get('target',0),a.get('fall_ticks',0)))
        if a.get('program_linear'):
            for step in a['steps']:
                _,kind,_,_,base,count,rise,target,fall,_,_,flags=step
                if flags&1:checks.append((a['segments'][base:base+count],kind==1,rise,target,fall))
        for segments,gate,rise,target,fall in checks:
            if any(n<32 for _,n in segments):return False
            if gate and (segments[rise-1][0]!=target or segments[-1][0]!=a['initial'] or sum(n for _,n in segments[rise:])!=fall):return False
        return True


CORE_HZ = 200_000_000
