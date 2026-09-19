"""Read old experiments without exposing obsolete controls in the current designer."""
import copy
import json
import uuid
from .model import Profile,Segment,Capabilities,compile_profile


def is_legacy_draft(draft):
    if not isinstance(draft,dict):return False
    profile=draft.get('profile');combos=draft.get('combos',{})
    return isinstance(profile,dict) and (profile.get('mode','segments')=='segments' or
        isinstance(combos,dict) and combos.get('mode')=='segments')


def recoverable_drafts(root):
    """Include preserved designer drafts, so archiving never hides unsaved experiments."""
    result=[];seen=set()
    paths=[root/'dac_editors.json',root/'designer_draft.json',*sorted((root/'archive').glob('legacy_designer_draft_*.json'))]
    for path in paths:
        try:
            data=json.loads(path.read_text(encoding='utf-8'))
            drafts=data.get('drafts',{}) if isinstance(data,dict) else {}
            if not isinstance(drafts,dict):continue
            for key,draft in sorted(drafts.items()):
                if key not in [str(i) for i in range(8)] or not isinstance(draft,dict):continue
                legacy=is_legacy_draft(draft)
                if path.name!='dac_editors.json' and not legacy:continue
                signature=json.dumps(draft,sort_keys=True,ensure_ascii=False)
                if signature in seen:continue
                seen.add(signature)
                name=draft.get('texts',{}).get('name',draft.get('profile',{}).get('name','历史草稿'))
                title=f'{len(result)+1}. DAC {int(key)+1} · {name}'+(' · 历史分段' if legacy else '')
                result.append((title,draft))
        except (OSError,ValueError,TypeError,AttributeError):continue
    return result


def draft_snapshot(draft):
    p=Profile.from_dict(draft['profile'])
    for key in ('initial','safe','update_us','repeat','fm_initial','fm_step','fm_points'):
        if key in draft.get('values',{}):setattr(p,key,draft['values'][key])
    p.name=draft.get('texts',{}).get('name',p.name)
    p.trigger=draft.get('combos',{}).get('trigger',p.trigger)
    if 'table' in draft:p.segments=[Segment(int(row[1]),float(row[2])) for row in draft['table']]
    p.mode='segments';compile_profile(p,Capabilities(dac_bits=p.dac_bits))
    return p


def ramp_copy(profile):
    """Explicit shape migration; never overwrite the old experiment or claim sample identity."""
    if profile.mode!='segments':raise ValueError('请选择历史分段方案')
    compile_profile(profile,Capabilities(dac_bits=profile.dac_bits))
    segments=profile.segments
    if not (len(segments)==3 and segments[0].target==segments[1].target and
            segments[2].target==profile.initial and segments[0].target!=profile.initial):
        raise ValueError('此方案不是进入 / 保持 / 返回的单幅度三段结构；请在执行序列中重新编排，原方案可继续查看和导出')
    p=copy.deepcopy(profile);p.mode='ramp';p.id=uuid.uuid4().hex
    p.name=profile.name[:85]+' · Ramp 副本';p.channel=0
    p.amplitude=segments[0].target
    p.rise_ms,p.hold_ms,p.fall_ms=(segment.duration_ms for segment in segments)
    p.timing_mode='fixed';p.trigger_input=0;p.trigger_edge='rising'
    p.fm_initial=0;p.fm_step=1;p.fm_points=1024
    compile_profile(p,Capabilities(dac_bits=p.dac_bits))
    return p
