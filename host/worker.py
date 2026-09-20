from PySide6.QtCore import QObject, Signal, Slot, QTimer
from .protocol import Device, SerialTransport
from .simulator import Simulator
from .parallel_simulator import ParallelSimulator


class DeviceWorker(QObject):
    result = Signal(str, object)
    failed = Signal(str, str)
    status = Signal(object)
    progress = Signal(str)
    applied = Signal(object)
    capabilities = Signal(object)
    resources = Signal(object)
    trigger_status = Signal(object)

    def __init__(self):
        super().__init__()
        self.device = None
        self.timer = None
        self.failures = 0

    @Slot(str)
    def set_dac_model(self,model):
        if self.device:self.device.dac_model=model

    @Slot(str, object)
    def execute(self, operation, argument):
        try:
            if self.device and self.device.debug_active and operation in ('upload','prepare','upload_many','compile','compile_sequence','resources','static'):
                raise ValueError('请先结束触发调试，再编译或下载方案；避免长操作中断调试心跳')
            if operation == "connect":
                self.disconnect()
                if isinstance(argument,dict):
                    if argument.get('port')=='demo':
                        transport=(ParallelSimulator if argument['dac_count']>=3 else Simulator)(argument['dac_count'],argument['trigger_count'],argument.get('dac_bits',10),core_hz=argument.get('core_hz',200_000_000))
                    elif str(argument.get('port','')).startswith('usb:'):
                        from .usb_transport import USBTransport
                        transport=USBTransport(argument['port'])
                    else:transport=SerialTransport(argument['port'],argument.get('baudrate',115200))
                else:transport=Simulator() if argument=='demo' else SerialTransport(argument)
                try:
                    self.device = Device(transport,dac_model=argument.get('dac_model','') if isinstance(argument,dict) else '')
                except Exception:
                    transport.close()
                    raise
                self.timer = QTimer(self)
                self.timer.timeout.connect(self.poll)
                self.timer.start(150)
                self.failures = 0
                self.capabilities.emit(self.device.caps)
                self.result.emit(operation, self.device.caps)
            elif operation == "disconnect":
                self.disconnect()
                self.result.emit(operation, None)
            elif operation == 'capabilities':
                if self.device is None: raise RuntimeError('请先连接设备')
                caps=self.device.refresh_capabilities()
                self.capabilities.emit(caps)
                self.result.emit(operation,caps)
            elif operation == 'resources':
                from .resource_usage import assess_resources
                if isinstance(argument,dict):
                    profiles=argument['profiles'];caps=argument.get('caps')
                else:
                    profiles=argument if isinstance(argument,(list,tuple)) else [argument]
                    caps=None
                if self.device:
                    caps=self.device.refresh_capabilities();self.capabilities.emit(caps)
                if caps is None:raise RuntimeError('请先连接设备或提供离线板卡配置')
                reports=tuple(assess_resources(p,caps) for p in profiles)
                self.resources.emit(reports)
                self.result.emit(operation,reports)
            elif operation in ("compile","compile_sequence"):
                from .functions import compile_samples
                profile=argument['profile'] if isinstance(argument,dict) else argument
                requested_caps=argument.get('caps') if isinstance(argument,dict) else None
                self.progress.emit("正在完整离散、取整和检查容量…")
                if self.device:
                    from .resource_usage import preflight_resources
                    preflight_resources(self.device,[profile],self.progress.emit,self.resources.emit,self.capabilities.emit)
                    requested_caps=self.device.caps
                compiled=compile_samples(profile,max_runs=requested_caps.max_runs if requested_caps else None,min_ticks=requested_caps.interpolation_min_ticks if requested_caps else 32,sample_hz=requested_caps.core_hz if requested_caps else 200_000_000)
                self.result.emit(operation,compiled)
            elif operation == "upload":
                from .output_prepare import verified_upload
                identity = verified_upload(self.device,argument,self.progress.emit,self.applied.emit,resources=self.resources.emit,capabilities=self.capabilities.emit)
                self.result.emit(operation, identity)
                self.poll()
            elif operation == 'prepare':
                from .output_prepare import prepare_outputs
                result=prepare_outputs(self.device,argument,self.progress.emit,self.applied.emit,resources=self.resources.emit,capabilities=self.capabilities.emit)
                self.poll();self.result.emit(operation,result)
            elif operation == "upload_many":
                from .resource_usage import preflight_resources
                channels=[p.channel for p in argument]
                if not channels or len(channels)!=len(set(channels)):raise ValueError('请选择不同的 DAC 通道')
                preflight_resources(self.device,argument,self.progress.emit,self.resources.emit,self.capabilities.emit)
                if not self.device.caps.parallel_channels: raise ValueError('当前固件不支持多路并行')
                for profile in argument:
                    if self.device.status(profile.channel)['state'] not in (0,3):
                        raise ValueError(f'DAC {profile.channel+1} 仍在运行或等待触发')
                identities=[]
                from .output_prepare import verified_upload
                for profile in argument:
                    self.progress.emit(f'正在下载 DAC {profile.channel+1}：{profile.name}')
                    identities.append(verified_upload(self.device,profile,self.progress.emit,self.applied.emit,resources=self.resources.emit,capabilities=self.capabilities.emit))
                self.result.emit(operation,identities);self.poll()
            elif operation == "group":
                self.device.group_command(**argument)
                self.result.emit(operation,argument);self.poll()
            elif operation == "static":
                if self.device is None: raise RuntimeError('请先连接设备')
                result=self.device.set_static(**argument)
                self.result.emit(operation,result)
                self.poll()
            elif operation == 'trigger_debug':
                if self.device is None:raise RuntimeError('请先连接设备')
                from .trigger_debug_control import control_trigger
                result=control_trigger(self.device,argument,self.progress.emit)
                self.trigger_status.emit(result)
                self.result.emit(operation,result);self.poll()
            elif operation == "external":
                if not isinstance(self.device.transport, (Simulator,ParallelSimulator)):
                    raise ValueError("仅演示设备可模拟外部触发")
                self.device.transport.external_trigger(*(argument or (None,None)))
                self.result.emit(operation, None)
            elif operation == "command":
                if isinstance(argument,dict): self.device.channel_command(**argument)
                else: self.device.command(argument)
                self.result.emit(operation, argument)
                self.poll()
        except Exception as exc:
            if operation in ('prepare','upload','upload_many','group','command','static','trigger_debug'):self.poll()
            self.failed.emit(operation, str(exc))

    def poll(self):
        if not self.device:
            return
        try:
            if self.device.caps.trigger_debug:
                self.trigger_status.emit(self.device.trigger_debug())
            if self.device.caps.parallel_channels:
                states=self.device.status_all()
                self.status.emit({**states[0],'channels':states})
            else:self.status.emit(self.device.status())
            self.failures = 0
        except Exception as exc:
            self.failures += 1
            if self.failures >= 2:
                self.disconnect()
                self.failed.emit("connection_lost", str(exc))

    def disconnect(self):
        if self.timer:
            self.timer.stop()
            self.timer.deleteLater()
            self.timer = None
        if self.device:
            device=self.device
            self.device=None
            try:
                if device.debug_active:device.trigger_debug('stop')
            except Exception as exc:
                self.progress.emit(f'主动结束调试未获确认：{exc}；无后续心跳时 FPGA 约 3 秒后自动中止')
            finally:
                try:device.close()
                except Exception as exc:self.progress.emit(f'连接句柄关闭异常：{exc}；本地已断开')
