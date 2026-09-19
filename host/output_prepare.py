"""Validate the entire operation before writing, then verify each commit and arm."""
from .model import compile_profile,fingerprint
from .protocol import ARM
from .resource_usage import preflight_resources


def verified_upload(device,profile,progress,applied,*,resources=lambda reports:None,capabilities=lambda caps:None):
    preflight_resources(device,[profile],progress,resources,capabilities)
    identity=device.upload(profile,progress)
    status=device.status(profile.channel)
    if not (status['valid'] and status['locked'] and status['config_id']==identity and status['channel']==profile.channel):
        raise RuntimeError(f'DAC {profile.channel+1} 提交后回读不一致，未确认加载版本')
    if profile.mode=='scan' and not (status.get('scan_mode') and status['state']==0 and status.get('scan_position')==0 and status['code']==profile.initial):
        raise RuntimeError(f'DAC {profile.channel+1} 扫描模式或初始状态未确认，未继续布防')
    applied(dict(profile=profile,config_id=identity,status=status))
    return identity


def prepare_outputs(device,argument,progress=lambda text:None,applied=lambda data:None,*,resources=lambda reports:None,capabilities=lambda caps:None):
    profiles=argument['profiles'];confirmed=argument.get('confirmed',{})
    channels=[p.channel for p in profiles]
    if not channels or len(channels)!=len(set(channels)):raise ValueError('请选择不同的 DAC 通道')
    preflight_resources(device,profiles,progress,resources,capabilities)
    if not device.caps.parallel_channels and len(channels)!=1:raise ValueError('当前固件只支持单通道准备')
    changed=[];identities={};uploaded=0
    # Compile every changed profile before the first BEGIN/COMMIT, so a capacity
    # failure in a later channel cannot replace an earlier channel's waveform.
    for p in profiles:
        compile_profile(p,device.caps);status=device.status(p.channel)
        if not status['locked']:raise ValueError(f'DAC {p.channel+1} 时钟未锁定')
        match=(status['valid'] and status['channel']==p.channel and confirmed.get(p.channel)==(status['config_id'],fingerprint(p)))
        if status['state'] not in (0,3) and not (match and status['state']==1):
            raise ValueError(f'DAC {p.channel+1} 正在运行或等待旧版本触发，请先中止或取消布防')
        if not match:
            progress(f'完整检查 DAC {p.channel+1}：{p.name}')
            changed.append(p)
        else:identities[p.channel]=status['config_id']
    try:
        for p in changed:
            status=device.status(p.channel)
            if status['state'] not in (0,3):raise RuntimeError(f'DAC {p.channel+1} 状态已变化，请重试')
            identities[p.channel]=verified_upload(device,p,progress,applied,resources=resources,capabilities=capabilities);uploaded+=1
        arm=[]
        for p in profiles:
            status=device.status(p.channel)
            if not (status['valid'] and status['locked'] and status['channel']==p.channel and status['config_id']==identities[p.channel]):
                raise RuntimeError(f'DAC {p.channel+1} 配置已变化，未继续布防')
            if status['state'] in (0,3):arm.append(p.channel)
            elif status['state']!=1:raise RuntimeError(f'DAC {p.channel+1} 已被触发，未继续布防其他通道')
        if arm:
            if device.caps.parallel_channels:device.group_command(ARM,arm)
            else:device.channel_command(ARM,arm[0])
        return dict(uploaded=uploaded,reused=len(profiles)-uploaded,channels=channels)
    except Exception as exc:
        raise RuntimeError(f'准备未完成：{exc}。已确认下载 {uploaded} 路，请核对各通道状态；不会自动回退已提交配置。') from exc
