"""Output assignments and verified device snapshots, independent of page widgets."""
import copy
import json
from dataclasses import replace
from .model import Profile, fingerprint
from .boot import matches_boot


def assigned_profile(profile, channel, assignment):
    p=replace(copy.deepcopy(profile),channel=channel)
    # Event programs own their input mapping at each step.
    if not (p.mode=='sequence' and p.sequence_mode=='events'):
        for key in ('trigger','trigger_input','trigger_edge'):
            if key in assignment.get('trigger_override',{}):
                setattr(p,key,assignment['trigger_override'][key])
        if p.timing_mode=='gate':p.trigger='external';p.trigger_edge='rising'
    return p


def trigger_description(p):
    if not p:return '尚未选择方案'
    if p.mode=='scan':
        return '触发步进 · '+('软件单步' if p.trigger=='software' else f'输入 {p.trigger_input+1} · '+('上升沿' if p.trigger_edge=='rising' else '下降沿'))
    if p.mode=='sequence' and p.sequence_mode=='events':
        inputs=sorted({s.get('input',0)+1 for s in p.sequence_steps if s.get('kind') in ('edge','gate')})
        return '逐步触发 · 输入 '+('/'.join(map(str,inputs)) or '按步骤设置')
    if p.trigger=='software':return '软件触发'
    return f'输入 {p.trigger_input+1} · '+('上升进入 / 高电平保持 / 下降返回' if p.timing_mode=='gate' else '上升沿' if p.trigger_edge=='rising' else '下降沿')


class OutputState:
    def __init__(self,library):
        self.library=library;self.path=library.root/'parallel.json'
        self.assignments=self.read(self.path,{})
        self.profiles=[];self.identities={};self.snapshots={};self.connection=None
        self.cache_path=library.root/'loaded_waveforms.json'
        self.cache=self.read(self.cache_path,{})
        self.refresh_library()

    @staticmethod
    def read(path,default):
        try:
            data=json.loads(path.read_text(encoding='utf-8'))
            return data if isinstance(data,dict) else default
        except (ValueError,OSError):return default

    @staticmethod
    def write(path,data):
        temp=path.with_suffix('.tmp');temp.write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8');temp.replace(path)

    def refresh_library(self):
        self.profiles=[p for p in self.library.list() if p.mode in ('ramp','function','sequence','scan')]

    def assignment(self,ch):return self.assignments.get(str(ch),{})

    def assign(self,ch,profile_id,enabled=None):
        data=dict(self.assignment(ch))
        if data.get('profile')!=profile_id:data.pop('trigger_override',None)
        data['profile']=profile_id
        if enabled is not None:data['enabled']=bool(enabled)
        self.assignments[str(ch)]=data

    def save(self):self.write(self.path,self.assignments)

    def resolve(self,ch):
        assignment=self.assignment(ch)
        p=next((p for p in self.profiles if p.id==assignment.get('profile')),None)
        return assigned_profile(p,ch,assignment) if p else None

    def override(self,ch,values):
        self.assignments.setdefault(str(ch),{})['trigger_override']=values;self.save()

    def connect(self,key):
        self.connection=key;self.identities.clear();self.snapshots.clear()
        if key and key!='demo':
            for record in self.cache.get(key,[]):
                try:
                    p=Profile.from_dict(record['profile'])
                    self.snapshots[(p.channel,record['config_id'])]=(p,record['bits'])
                except (KeyError,ValueError,TypeError):continue

    def confirm(self,profile,config_id,caps):
        p=copy.deepcopy(profile)
        self.snapshots[(p.channel,config_id)]=(p,caps.dac_bits)
        self.identities[p.channel]=(config_id,fingerprint(p))
        if self.connection and self.connection!='demo':
            self.cache[self.connection]=[dict(config_id=key[1],profile=value[0].to_dict(),bits=value[1])
                for key,value in list(self.snapshots.items())[-128:]]
            self.write(self.cache_path,self.cache)

    def loaded(self,ch,status,caps,boot_profiles=None):
        if not status or not status.get('valid') or status.get('channel')!=ch:return None
        record=self.snapshots.get((ch,status.get('config_id')))
        if record and record[1]==caps.dac_bits:return copy.deepcopy(record[0])
        if status.get('boot_profile'):
            candidates=[self.resolve(ch)]
            try:
                data=(boot_profiles or {}).get(str(ch))
                if data:candidates.insert(0,Profile.from_dict(data))
            except (ValueError,TypeError,KeyError):pass
            for p in candidates:
                if p and p.channel==ch and p.dac_bits==caps.dac_bits and matches_boot(p,status,caps):return copy.deepcopy(p)
        return None

    def matches(self,p,status,caps):
        return bool(p and status and status.get('valid') and status.get('channel')==p.channel and
            (self.identities.get(p.channel)==(status.get('config_id'),fingerprint(p)) or
             (self.loaded(p.channel,status,caps) is not None and fingerprint(self.loaded(p.channel,status,caps))==fingerprint(p)) or
             matches_boot(p,status,caps)))
