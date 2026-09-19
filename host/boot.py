"""Compile saved per-DAC snapshots into bitstream-initialized playback RAM."""
import hashlib
import json
from pathlib import Path
from dataclasses import replace
from .model import Profile, Capabilities, compile_profile, fingerprint
from .functions import compile_samples


def capacity(count):
    return 8192 if count == 1 else 4096 if count == 2 else 2048 if count <= 4 else 1024


def identity(profile, sample_hz=200_000_000):
    rate_tag="" if sample_hz==200_000_000 else f"{sample_hz}:"
    return int.from_bytes(hashlib.sha256(('ramp-boot-v1:' + rate_tag + fingerprint(profile)).encode()).digest()[:4], 'little') or 1


def snapshot_assignments(library, count, channels=None):
    path=library.root/'parallel.json'
    assignments=json.loads(path.read_text(encoding='utf-8')) if path.exists() else {}
    profiles={p.id:p for p in library.list()}
    result={}
    for ch in range(count):
        selected=assignments.get(str(ch),{})
        included=ch in channels if channels is not None else selected.get('enabled',True)
        if not included or not selected.get('profile'):continue
        p=profiles.get(selected['profile'])
        if p is None:raise ValueError(f'DAC {ch+1} 的方案已不存在，请重新选择')
        from .output_state import assigned_profile
        result[str(ch)]=assigned_profile(p,ch,selected).to_dict()
    if not result:raise ValueError('请先为 DAC 选择已保存方案，并在 Flash 页面勾选纳入范围')
    return result


def generate_boot(board, directory):
    """Runs in the build worker; fully validates/quantizes before invoking Vivado."""
    from .board import clock_ports, validate
    errors=validate(board)
    if errors:raise ValueError('\n'.join(errors))
    directory=Path(directory);directory.mkdir(parents=True,exist_ok=False)
    caps=Capabilities(core_hz=int(board.core_mhz*1e6),dac_bits=board.dac_bits,dac_count=board.dac_count,trigger_count=board.trigger_count,
                      max_runs=capacity(board.dac_count),parallel_channels=True,sequence_steps=64,infinite_repeat=True,trigger_scan=True,interpolation=True)
    configs=files=0;manifest=[]
    for key,data in sorted(board.boot_profiles.items()):
        ch=int(key)
        if str(ch)!=key or not 0<=ch<board.dac_count:raise ValueError('上电方案 DAC 编号与板卡不一致')
        p=Profile.from_dict(data)
        if p.channel!=ch:raise ValueError('上电方案通道编号不一致')
        compile_profile(p,caps);samples=compile_samples(p,max_runs=caps.max_runs,min_ticks=caps.interpolation_min_ticks,sample_hz=caps.core_hz)
        if len(samples.runs)>caps.max_runs:raise ValueError(f'DAC {ch+1} 需要 {len(samples.runs)} 段，容量为 {caps.max_runs}')
        gate=p.timing_mode=='gate';program=p.mode=='sequence' and p.sequence_mode=='events';cfgid=identity(p,caps.core_hz)
        cfg=1 | (int(program)<<4) | (int(gate)<<1) | (int(p.trigger=='external')<<2) | (int(p.trigger_edge=='falling')<<3) | (int(getattr(samples,'interpolation',False))<<7)
        for value,shift in ((p.initial,16),(p.safe,32),(p.repeat,48),(cfgid,64),(len(samples.runs),96),
                            (getattr(samples,'rise_runs',0),112),(getattr(samples,'fall_ticks',1),128),
                            (p.amplitude,160),(p.trigger_input,176)):
            cfg|=value<<shift
        if program:cfg|=len(samples.steps)<<184
        if p.mode=='scan':
            cfg|=(1<<5) | (int(p.scan_end=='loop')<<6) | (p.scan_start<<192) | ((p.scan_step&65535)<<208) | (p.scan_points<<224)
        configs|=cfg<<(256*ch)
        filename=f'boot_dac{ch}.mem'
        files|=int.from_bytes(filename.encode('ascii'),'big')<<(2048*ch)
        # Both banks are initialized so synthesis and simulation agree. Bank one
        # remains available for normal UART uploads without rebuilding firmware.
        words=[(code<<32)|ticks for code,ticks in samples.runs]
        words.extend([0]*(2*caps.max_runs-len(words)))
        memory=''.join(f'{word:0{(board.dac_bits+35)//4}x}\n' for word in words)
        (directory/filename).write_text(memory,encoding='ascii')
        if program:
            from .sequences import pack_step
            words=[int.from_bytes(pack_step(s),'little') for s in samples.steps]+[0]*(128-len(samples.steps))
            (directory/(filename+'.steps')).write_text(''.join(f'{word:048x}\n' for word in words),encoding='ascii')
        manifest.append(dict(channel=ch,profile=p.to_dict(),config_id=cfgid,runs=len(samples.runs),
                             samples=samples.total,sample_hz=caps.core_hz,playback='linear' if getattr(samples,'interpolation',False) else 'hold',
                             interpolation_error=getattr(samples,'error_limit',None),checked_samples=getattr(samples,'checked_samples',0),
                             max_error=samples.max_error,memory_sha256=hashlib.sha256(memory.encode()).hexdigest()))
    inputs=','.join(clock_ports(board.clock_input))
    module='ramp_top_diff' if board.clock_input=='differential' else 'ramp_top'
    from .hardware_interfaces import UsbFifoSettings
    from .extended_build import USB_DECL, USB_NAMES
    usb=UsbFifoSettings.from_dict(board.usb_fifo).enabled
    if usb:module='ramp_extended_top'
    extra_ports=', '+USB_DECL if usb else ''
    extra_connections=','+','.join(f'.{name}({name})' for name in USB_NAMES) if usb else ''
    platform_parameters='' if usb else f'.CORE_HZ({int(board.core_mhz*1e6)}),.DAC_COUNT({board.dac_count}),.TRIGGER_COUNT({board.trigger_count}),.DAC_BITS({board.dac_bits}),.UART_BAUD({board.uart_baud}),'
    clock_connections=','.join(f'.{p}({p})' for p in clock_ports(board.clock_input))
    wrapper=f'''// Generated immutable boot snapshot; do not edit.
module ramp_boot_top(input {inputs},input [{board.trigger_count-1}:0] trigger_in,input uart_rx,
 output uart_tx,gnd0,gnd1,output [{board.dac_count*board.dac_bits-1}:0] dac_data,output [{board.dac_count-1}:0] dac_clk{extra_ports});
 {module} #({platform_parameters}
 .BOOT_CONFIGS(2048'h{configs:0512x}),.BOOT_FILES(16384'h{files:04096x})) core(
 {clock_connections},.trigger_in(trigger_in),.uart_rx(uart_rx),.uart_tx(uart_tx),
 .gnd0(gnd0),.gnd1(gnd1),.dac_data(dac_data),.dac_clk(dac_clk){extra_connections});
endmodule
'''
    (directory/'ramp_boot_top.v').write_text(wrapper,encoding='ascii')
    (directory/'boot_manifest.json').write_text(json.dumps(dict(schema=1,part=board.part,dac_bits=board.dac_bits,
        dac_count=board.dac_count,trigger_count=board.trigger_count,clock_input=board.clock_input,ref_mhz=board.ref_mhz,core_mhz=board.core_mhz,uart_baud=board.uart_baud,
        clock_io_standard=board.clock_io_standard,clock_diff_term=board.clock_diff_term,channels=manifest),ensure_ascii=False,indent=2),encoding='utf-8')
    return manifest


def matches_boot(profile,status,caps):
    return bool(profile and caps.boot_profiles and status and status.get('valid') and status.get('boot_profile')
                and status.get('config_id')==identity(profile,caps.core_hz))
