"""Host-side handover of already armed profiles to virtual trigger inputs."""
import time
from .protocol import ARM, ABORT
from .model import integer


def control_trigger(device, argument, progress=lambda message: None):
    params = dict(argument)
    operation = params.get('operation', 'status')
    if operation not in ('enable', 'enable_all', 'high', 'low', 'pulse', 'periodic'):
        return device.trigger_debug(**params)
    device.refresh_capabilities()
    if not device.caps.trigger_debug:
        raise ValueError('当前固件不支持真实触发调试，请加载新版 BIT')
    index = params.get('input_index', 0)
    integer(index, '触发输入', 0, device.caps.trigger_count-1)
    if operation in ('pulse', 'periodic'):
        for key in ('high_ticks', 'period_ticks'):
            integer(params.get(key, 1 if key == 'high_ticks' else 2), key, 1, 0xffffffff)
        integer(params.get('count', 1), '脉冲次数', 0, 0xffffffff)
        if operation == 'periodic' and params.get('period_ticks', 2) <= params.get('high_ticks', 1):
            raise ValueError('周期必须大于高电平时间')
    debug = device.trigger_debug()
    wanted = (1 << device.caps.trigger_count)-1 if operation == 'enable_all' else 1 << index
    # Repeated actions must never disarm a waveform already using our input.
    if device.debug_active and debug['mask'] & wanted == wanted:
        return debug if operation in ('enable', 'enable_all') else device.trigger_debug(**params)
    if debug['mask'] and not device.debug_active:
        raise ValueError('触发输入由其他连接接管，请先结束原调试')
    if debug.get('running'):
        raise ValueError('请先结束周期脉冲，再更换触发输入')
    states = device.status_all()
    if any(s['state'] not in (0, 1, 3) for s in states):
        raise ValueError('有 DAC 正在播放，请先停止后再切换软件触发；现有波形未修改')
    armed = [s['channel'] for s in states if s['state'] == 1]
    identities = {s['channel']: s['config_id'] for s in states}
    progress('接管触发输入，保留已加载波形并重新布防等待中的通道；扫描和事件序列从头开始')
    try:
        if armed:
            device.group_command(ABORT, armed)
        # Source handover is still checked atomically by the FPGA. A physical
        # edge racing the snapshot cannot bypass its stopped-channel rule.
        device.trigger_debug('enable_all' if operation == 'enable_all' else 'enable', index)
        if armed:
            device.group_command(ARM, armed)
            deadline = time.monotonic() + 2
            while True:
                device.trigger_debug()  # Keep watchdog alive even on slow UART.
                current = device.status_all()
                if any(s['config_id'] != identities[s['channel']] for s in current):
                    raise RuntimeError('接管期间设备波形发生变化，已停止调试')
                if all(s['state'] == 1 and s['prepared'] for s in current if s['channel'] in armed):
                    break
                if time.monotonic() >= deadline:
                    raise TimeoutError('重新布防尚未就绪，已停止调试；未发送触发')
        return device.trigger_debug() if operation in ('enable', 'enable_all') else device.trigger_debug(**params)
    except Exception:
        # Never leave partially rearmed outputs or a half-completed takeover.
        try:
            device.trigger_debug('stop')
        except Exception:
            pass  # Firmware watchdog handles loss of communication.
        raise
