"""Independent demo channels using the same SELECT/GROUP protocol as the FPGA."""
import struct
from .simulator import Simulator
from .protocol import *


class ParallelSimulator:
    def __init__(self,dac_count=4,trigger_count=2,dac_bits=10,interpolation=True,core_hz=200_000_000):
        self.channels=[Simulator(1,trigger_count,dac_bits,interpolation,core_hz) for _ in range(dac_count)]
        self.selected=0;self.cache=None;self.dac_count=dac_count
        self.capacity=8192 if dac_count==1 else 4096 if dac_count<=2 else 2048 if dac_count<=4 else 1024

    def close(self): pass

    def external_trigger(self,index=None,edge=None):
        for channel in self.channels:channel.external_trigger(index,edge)

    def exchange(self,frame):
        if self.cache and frame==self.cache[0]:return self.cache[1]
        cmd=frame[3];p=bytearray(frame[6:30]);seq=struct.unpack_from('<H',frame,4)[0]
        reply=bytearray(24)
        channel=self.channels[self.selected]
        if cmd==INFO:
            reply[:]=channel.exchange(frame)[6:30]
            struct.pack_into('<H',reply,11,2)  # Parallel-controller family, matching RTL; not a physical part ID.
            struct.pack_into('<H',reply,13,self.capacity);reply[15]=self.dac_count;reply[17]=239;reply[18]=64
        elif cmd==SELECT_CHANNEL:
            if p[0]>=self.dac_count:reply[0]=3
            else:self.selected=p[0]
        elif cmd==GROUP_COMMAND:
            sub,mask=p[:2];targets=[c for i,c in enumerate(self.channels) if mask&(1<<i)]
            if not mask or mask>>self.dac_count:reply[0]=3
            elif sub not in (ARM,TRIGGER,ABORT):reply[0]=1
            else:
                for c in targets:
                    c.refresh()
                    if sub==ARM and (c.state in (1,2) or not c.active):reply[0]=2 if c.state in (1,2) else 4
                    if sub==TRIGGER and (c.state!=1 or c.active['external']):reply[0]=2
                if not reply[0]:
                    for c in targets:
                        c.cache=None
                        r=c.exchange(encode(sub,seq));assert r[6]==0
                    if sub==TRIGGER:
                        # One shared timestamp models the FPGA's common start edge.
                        for c in targets:c.started=targets[0].started
        elif cmd in (BEGIN,SEGMENT):reply[0]=1
        else:
            if cmd==BEGIN_SCAN:
                if p[14]!=self.selected:reply[0]=3
                p[14]=0
            if cmd in (BEGIN_RUNS,BEGIN_GATE,BEGIN_SEQUENCE,BEGIN_LINEAR,BEGIN_LINEAR_GATE,BEGIN_SEQUENCE_LINEAR):
                offset=13 if cmd in (BEGIN_RUNS,BEGIN_LINEAR) else 12
                if p[offset]!=self.selected or struct.unpack_from('<H',p,4)[0]>self.capacity:reply[0]=3
                if cmd in (BEGIN_RUNS,BEGIN_LINEAR) and not p[16]:p[14]=p[13];p[15]=0;p[16]=1
                p[offset]=0
            if not reply[0]:
                channel.cache=None
                reply[:]=channel.exchange(encode(cmd,seq,p))[6:30]
                if cmd==STATUS:reply[17]=self.selected
        response=encode(cmd|128,seq,reply);self.cache=(frame,response)
        return response
