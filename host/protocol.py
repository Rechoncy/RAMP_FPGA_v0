"""32-byte protocol; byte order and command layout match rtl/common/controller.v."""
from __future__ import annotations
import binascii
import struct
import time
import secrets
from .model import Capabilities, compile_profile, integer
from .uart_settings import BAUD_RATES

INFO, STATUS, BEGIN, SEGMENT, COMMIT = 1, 2, 0x10, 0x11, 0x12
BEGIN_RUNS, RUN = 0x30, 0x31
BEGIN_GATE=0x32
BEGIN_SCAN=0x33
BEGIN_LINEAR,BEGIN_LINEAR_GATE=0x34,0x35
RUN_BATCH=0x36
BEGIN_SEQUENCE,WRITE_STEP=0x50,0x51
BEGIN_SEQUENCE_LINEAR=0x52
SELECT_CHANNEL,GROUP_COMMAND=0x40,0x41
DEBUG_CONFIG,DEBUG_ACTION,DEBUG_STATUS,DEBUG_STOP=0x60,0x61,0x62,0x63
ARM, TRIGGER, ABORT, DISARM = 0x20, 0x21, 0x22, 0x23
ERRORS = {1: "命令或版本不支持", 2: "当前状态不允许此操作", 3: "参数超出范围", 4: "方案未完整下载"}
STATES = {0: "空闲", 1: "等待触发", 2: "运行中", 3: "已完成"}


def crc(data):
    return binascii.crc_hqx(data, 0xFFFF)


def encode(command, sequence, payload=b""):
    if len(payload) > 24:
        raise ValueError("payload exceeds 24 bytes")
    raw = b"\xa5\x5a" + struct.pack("<BBH", 1, command, sequence) + payload.ljust(24, b"\0")
    return raw + struct.pack("<H", crc(raw[2:]))


class FrameParser:
    def __init__(self):
        self.buffer = bytearray()

    def feed(self, data):
        self.buffer.extend(data)
        frames = []
        while len(self.buffer) >= 2:
            pos = self.buffer.find(b"\xa5\x5a")
            if pos < 0:
                self.buffer[:] = self.buffer[-1:] if self.buffer[-1] == 0xA5 else b""
                break
            del self.buffer[:pos]
            if len(self.buffer) < 32:
                break
            frame = bytes(self.buffer[:32])
            if crc(frame[2:30]) != struct.unpack_from("<H", frame, 30)[0]:
                del self.buffer[0]
                continue
            frames.append(frame)
            del self.buffer[:32]
        return frames


class SerialTransport:
    def __init__(self, port, baudrate=115200):
        import serial
        if type(baudrate) is not int or baudrate not in BAUD_RATES:
            raise ValueError('不支持的串口波特率，请选择 9600～1,000,000 baud 的预设值')
        self.baudrate=baudrate
        self.serial = serial.Serial(port=port,baudrate=baudrate,bytesize=serial.EIGHTBITS,
                                    parity=serial.PARITY_NONE,stopbits=serial.STOPBITS_ONE,
                                    timeout=0.05,write_timeout=1,xonxoff=False,rtscts=False,dsrdtr=False)
        try:self.serial.reset_input_buffer()
        except Exception:
            self.serial.close();raise
        self.parser = FrameParser()

    def synchronize(self):
        """BREAK clears a stale frame; 0x55 trains new firmware, old UARTs ignore it.

        Called only on connection, never between commands or while downloading.
        Firmware changes only the link divider and leaves all DAC state intact.
        """
        self.serial.send_break(duration=0.02)
        training=b'\x55'*16
        if self.serial.write(training)!=len(training):
            raise IOError('串口同步序列未完整写入，请重新连接')
        self.serial.flush()
        # Some USB-UART drivers report flush complete when the USB packet is
        # queued, while their hardware FIFO is still serializing the bytes.
        # Budget the whole training burst (8N1 plus inter-byte allowance), then
        # a separate idle interval. Otherwise at 9600 the first INFO can follow
        # the last 0x55 with no idle gap, while the FPGA still pauses its RX.
        time.sleep(len(training)*12/self.baudrate + max(.01,24/self.baudrate))
        self.serial.reset_input_buffer();self.parser.buffer.clear()

    def exchange(self, packet):
        for _ in range(3):
            self.serial.write(packet)
            deadline = time.monotonic() + 0.7
            while time.monotonic() < deadline:
                # Responses are 32 bytes. Waiting for 64 needlessly spends the
                # full read timeout on every request, even at 1 Mbaud.
                data=self.serial.read(min(256,self.serial.in_waiting or 1))
                for frame in self.parser.feed(data):
                    if frame[2] == 1 and frame[3] == (packet[3] | 0x80) and frame[4:6] == packet[4:6]:
                        return frame
        raise TimeoutError(f"设备未应答（{self.baudrate:,} baud）；新固件可自动适配；旧固定波特率固件须选择匹配速率或先升级。操作结果未知，请读取状态或重新连接")

    def close(self):
        self.serial.close()


class Device:
    def __init__(self, transport, dac_model=""):
        self.dac_model = dac_model
        self.transport = transport
        self.sequence = secrets.randbelow(65536)
        self.selected_channel=0
        self.debug_active=False
        synchronize=getattr(transport,"synchronize",None)
        if synchronize is not None:synchronize()
        self.refresh_capabilities()

    def refresh_capabilities(self):
        """Read the loaded firmware's usable limits; INFO never alters playback."""
        p = self.command(INFO)
        hz, minimum, maximum, bits, dacdiv, board = struct.unpack_from("<IHHBBH", p, 1)
        if hz == 0 or minimum == 0 or maximum == 0 or dacdiv == 0 or bits not in (10,12,14):
            raise ValueError("设备能力无效或此界面暂不支持该 DAC 位数")
        dacs,triggers,routing = p[15] or 2,p[16] or 2,bool(p[17]&1)
        if not 1<=dacs<=8 or not 1<=triggers<=8: raise ValueError('设备通道数量超出当前界面支持范围')
        interpolation=bool(p[19]&1)
        if interpolation and (p[21]!=1 or p[20]<32):
            raise ValueError('设备插值协议版本或最短段时长不受支持')
        batch=bool(p[19]&2)
        if batch and (p[22]!=1 or not 1<=p[23]<=3):
            raise ValueError('设备批量下载协议版本或批量段数不受支持')
        if p[19]&8:
            raise ValueError('当前软件不支持该固件的外部存储扩展，请加载片内 BRAM 版本')
        caps = Capabilities(hz, minimum, maximum, bits, dacdiv, board, struct.unpack_from('<H',p,13)[0],dacs,triggers,routing,bool(p[17]&2),bool(p[17]&4),bool(p[17]&8),bool(p[17]&16),p[18] if p[17]&32 else 0,bool(p[17]&64),bool(p[17]&128),interpolation,p[20] if interpolation else 32,batch,bool(p[19]&4),p[23] if batch else 1,bool(p[19]&16),bool(p[19]&32))
        self.caps=caps
        return self.caps

    def check_output_clock(self):
        if self.dac_model=='3PD5651E' and (self.caps.core_hz/self.caps.dac_divider>125_000_000 or self.caps.dac_bits!=10):
            raise ValueError('当前固件不适用于 3PD5651E：需要 10 位且采样率不超过 125 MSPS；请先构建并烧录匹配固件')

    def command(self, cmd, payload=b""):
        if self.dac_model and (cmd in (ARM,TRIGGER) or cmd==GROUP_COMMAND and payload and payload[0] in (ARM,TRIGGER)):
            self.refresh_capabilities();self.check_output_clock()
        self.sequence = (self.sequence + 1) & 65535
        frame = self.transport.exchange(encode(cmd, self.sequence, payload))
        response = frame[6:30]
        if response[0]:
            raise RuntimeError(ERRORS.get(response[0], f"设备错误 {response[0]}"))
        return response

    def select_channel(self, channel):
        integer(channel,'DAC 通道',0,self.caps.dac_count-1)
        if self.caps.parallel_channels:
            self.command(SELECT_CHANNEL,bytes([channel]))
            self.selected_channel=channel

    def trigger_debug(self, operation='status', input_index=0, high_ticks=1, period_ticks=2, count=1):
        if not self.caps.trigger_debug:
            raise ValueError('当前固件不支持真实触发调试，请生成并临时加载新版 BIT')
        if operation in ('enable','enable_all'):
            integer(input_index,'触发输入',0,self.caps.trigger_count-1)
            mask=(1<<self.caps.trigger_count)-1 if operation=='enable_all' else 1<<input_index
            self.command(DEBUG_CONFIG,bytes([mask]));self.debug_active=True
        elif operation=='stop':
            self.command(DEBUG_STOP);self.debug_active=False
        elif operation!='status':
            actions={'low':0,'high':1,'pulse':2,'periodic':3}
            if operation not in actions:raise ValueError('未知触发调试操作')
            if not self.debug_active:raise ValueError('本次连接尚未接管输入；请先结束旧调试，再停止全部通道并接管输入')
            self.refresh_capabilities();self.check_output_clock()
            integer(input_index,'触发输入',0,self.caps.trigger_count-1)
            integer(high_ticks,'高电平时钟拍数',1,0xffffffff)
            integer(period_ticks,'周期时钟拍数',1,0xffffffff)
            integer(count,'脉冲次数',0,0xffffffff)
            if operation=='periodic' and period_ticks<=high_ticks:raise ValueError('周期必须大于高电平时间')
            self.command(DEBUG_ACTION,struct.pack('<BBIII',input_index,actions[operation],high_ticks,period_ticks,count))
        p=self.command(DEBUG_STATUS,bytes([int(self.debug_active)]))
        if p[9]!=1:raise ValueError('触发调试协议版本不支持')
        result=dict(mask=p[1],levels=p[2],running=bool(p[3]&1),expired=bool(p[3]&2),
                    input=p[4],emitted=struct.unpack_from('<I',p,5)[0])
        if not result['mask'] or result['expired']:self.debug_active=False
        result['owned']=self.debug_active
        return result

    def channel_command(self, command, channel):
        self.select_channel(channel)
        return self.command(command)

    def group_command(self, command, channels):
        if not self.caps.parallel_channels: raise ValueError('当前固件不支持多路并行控制')
        if command not in (ARM,TRIGGER,ABORT): raise ValueError('不支持的同步命令')
        if not channels or len(set(channels))!=len(channels): raise ValueError('请选择不同的 DAC 通道')
        for channel in channels: integer(channel,'DAC 通道',0,self.caps.dac_count-1)
        return self.command(GROUP_COMMAND,bytes([command,sum(1<<c for c in channels)]))

    def status_all(self):
        if not self.caps.parallel_channels: return [self.status()]
        return [self.status(c) for c in range(self.caps.dac_count)]

    def upload(self, profile, progress=lambda text:None, *, resources=lambda reports:None, capabilities=lambda caps:None):
        from .resource_usage import preflight_resources
        preflight_resources(self, [profile], progress, resources, capabilities)
        c = compile_profile(profile, self.caps)
        if self.caps.parallel_channels and profile.mode not in ('ramp','function','sequence','scan'):
            raise ValueError('并行固件请使用单幅度 Ramp 或自定义函数；旧版双通道 FM 不支持')
        config_id = secrets.randbelow(0xFFFFFFFF) + 1
        if profile.mode=='scan':
            from .scan import SCAN_FORMAT
            self.select_channel(profile.channel)
            flags=int(profile.trigger=='external') | (int(profile.trigger_edge=='falling')<<1) | (int(profile.scan_end=='loop')<<2)
            progress(f'配置 DAC {profile.channel+1} 触发步进：{profile.scan_points} 点')
            self.command(BEGIN_SCAN,struct.pack(SCAN_FORMAT,profile.initial,profile.safe,profile.scan_start,profile.scan_step,
                profile.scan_points,config_id,profile.channel,profile.trigger_input,flags))
            self.command(COMMIT)
            return config_id
        if profile.mode in ('ramp','function','sequence'):
            from .functions import compile_samples
            progress(f"正在按 {self.caps.core_hz/1e6:g} MSPS 完整离散与取整…")
            samples = compile_samples(profile,max_runs=self.caps.max_runs,min_ticks=self.caps.interpolation_min_ticks,sample_hz=self.caps.core_hz)
            linear=getattr(samples,'interpolation',False)
            if linear and not self.caps.interpolation:
                raise ValueError('当前固件不支持分段线性插值，请先更新 FPGA 固件')
            segment_name='插值/保持段' if profile.mode=='sequence' and linear else '插值段' if linear else '连续码段'
            if len(samples.runs) > self.caps.max_runs:
                raise ValueError(f'波形需要 {len(samples.runs)} 个{segment_name}，设备容量为 {self.caps.max_runs}')
            self.select_channel(profile.channel)
            if profile.mode=="sequence" and profile.sequence_mode=="events":
                from .sequences import pack_step
                self.command(BEGIN_SEQUENCE_LINEAR if linear else BEGIN_SEQUENCE,struct.pack("<HHHHIB",profile.initial,profile.safe,len(samples.runs),len(samples.steps),config_id,profile.channel))
                for step in samples.steps:self.command(WRITE_STEP,pack_step(step))
            elif profile.timing_mode=='gate':
                self.command(BEGIN_LINEAR_GATE if linear else BEGIN_GATE,struct.pack('<HHHHIBBHI',profile.initial,profile.safe,len(samples.runs),samples.rise_runs,
                    config_id,profile.channel,profile.trigger_input,profile.amplitude,samples.fall_ticks))
            else:
                self.command(BEGIN_LINEAR if linear else BEGIN_RUNS, struct.pack('<HHHHIBBBBB',profile.initial,profile.safe,len(samples.runs),profile.repeat,
                    config_id,profile.trigger=='external',profile.channel,profile.trigger_input,profile.trigger_edge=='falling',int(self.caps.trigger_routing)))
            self.upload_runs(samples.runs,lambda i,total:progress(
                f"下载 DAC {profile.channel+1}：{i}/{total} 个{segment_name}"))
            self.command(COMMIT)
            return config_id
        self.command(BEGIN, struct.pack("<HHHHIBxHHHH", profile.initial, c.divider,
            len(c.counts), profile.repeat, config_id, profile.trigger == "external", profile.safe,
            profile.fm_initial, profile.fm_step, profile.fm_points))
        for i, (s, n) in enumerate(zip(profile.segments, c.counts)):
            self.command(SEGMENT, struct.pack("<HHI", i, s.target, n))
        self.command(COMMIT)
        return config_id

    def upload_runs(self, runs, progress=lambda done,total:None):
        """Upload to the existing pending bank with acknowledged transactions.

        Capability negotiation is mandatory: old firmware receives RUN only.
        A failed batch is never silently retried as individual RUN commands.
        """
        size=self.caps.max_batch_runs if self.caps.run_batch else 1
        bulk=getattr(self.transport,'exchange_many',None)
        window=getattr(self.transport,'upload_window',1) if self.caps.run_batch else 1
        pending=[]
        def flush():
            if not pending:return
            replies=bulk(pending)
            if len(replies)!=len(pending):
                raise RuntimeError('USB 批量应答数量不一致，未提交新波形')
            for reply in replies:
                error=reply[6]
                if error:raise RuntimeError(ERRORS.get(error,f'设备错误 {error}'))
            pending.clear()
        for start in range(0,len(runs),size):
            if start//32!=(start-size)//32 or start==0:progress(start,len(runs))
            batch=runs[start:start+size]
            if self.caps.run_batch:
                payload=struct.pack('<HBB',start,len(batch),0)
                payload+=b''.join(struct.pack('<HI',code,ticks) for code,ticks in batch)
                if callable(bulk) and window>1:
                    self.sequence=(self.sequence+1)&65535
                    pending.append(encode(RUN_BATCH,self.sequence,payload))
                    if len(pending)==window:flush()
                else:self.command(RUN_BATCH,payload)
            else:
                code,ticks=batch[0]
                self.command(RUN,struct.pack('<HHI',start,code,ticks))
        flush()
        progress(len(runs),len(runs))

    def status(self, channel=None):
        if channel is not None: self.select_channel(channel)
        p = self.command(STATUS)
        state, seg, code, loops, identity, ignored = struct.unpack_from("<BBHHII", p, 1)
        result=dict(state=state, segment=seg, code=code, loops=loops, config_id=identity,
                    ignored=ignored, valid=bool(p[15]), locked=bool(p[16]), channel=p[17],
                    run_index=struct.unpack_from('<H',p,18)[0], sample_mode=bool(p[20]&1),interpolation_mode=bool(self.caps.interpolation and p[20]&2),
                    trigger_input=p[21] if self.caps.trigger_routing else p[17],
                    trigger_edge="falling" if self.caps.trigger_routing and p[22]&1 else "rising",
                    gate_mode=bool(self.caps.gate_mode and p[23]&128),gate_phase=p[23]&7 if self.caps.gate_mode else 0,
                    boot_profile=bool(self.caps.boot_profiles and p[23]&64),sequence_mode=bool(p[23]&32),sequence_step=p[2],prepared=bool(p[22]&128) if p[23]&32 or self.caps.interpolation and p[20]&2 else True,
                    sequence_phase=(p[22]>>1)&7 if p[23]&32 else 0,
                    infinite_repeat=bool(self.caps.infinite_repeat and p[23]&16 and not p[23]&8),
                    scan_mode=bool(self.caps.trigger_scan and p[23]&8),scan_loop=bool(p[23]&8 and p[23]&16),
                    scan_position=struct.unpack_from('<H',p,18)[0] if p[23]&8 else 0)
        return result

    def set_static(self, channel, code, safe):
        """Commit an idle constant configuration; never arm or trigger it."""
        integer(channel,'输出 DAC 编号（从 0 开始）',0,self.caps.dac_count-1)
        limit=(1<<self.caps.dac_bits)-1
        integer(code,'静态原码',0,limit); integer(safe,'中止码',0,limit)
        self.refresh_capabilities()
        integer(channel,'输出 DAC 编号（从 0 开始）',0,self.caps.dac_count-1)
        limit=(1<<self.caps.dac_bits)-1
        integer(code,'静态原码',0,limit);integer(safe,'中止码',0,limit)
        self.check_output_clock()
        from .sample_rates import supports_sample_clock
        if not supports_sample_clock(self.caps):
            raise ValueError('当前固件不支持静态输出，请使用支持对应采样时钟函数播放的固件')
        self.select_channel(channel)
        before=self.status()
        if not before['locked']: raise RuntimeError('设备时钟未锁定')
        if before['state'] not in (0,3):
            raise RuntimeError('设备正在运行或等待触发，请先中止或取消布防')
        identity=secrets.randbelow(0xFFFFFFFF)+1
        self.command(BEGIN_RUNS,struct.pack('<HHHHIBBBBB',code,safe,1,1,identity,0,channel,0,0,int(self.caps.trigger_routing)))
        self.command(RUN,struct.pack('<HHI',0,code,1))
        # COMMIT latches the initial code and issues DISARM in existing firmware.
        self.command(COMMIT)
        status=self.status()
        if not (status['locked'] and status['valid'] and status['sample_mode'] and status['state']==0
                and status['config_id']==identity and status['channel']==channel and status['code']==code):
            raise RuntimeError('静态输出已提交，但状态回读不一致；请查看设备状态，输出结果待确认')
        return dict(config_id=identity,channel=channel,code=code,safe=safe,status=status)

    def close(self):
        self.transport.close()
