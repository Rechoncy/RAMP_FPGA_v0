from .sample_rates import supports_sample_clock
"""Explicit, single-channel static DAC output with device status readback."""
from PySide6.QtCore import Signal
from PySide6.QtWidgets import QWidget,QVBoxLayout,QHBoxLayout,QLabel,QFrame,QComboBox,QSpinBox,QScrollArea
from .appearance import SoftButton
from .protocol import ABORT,DISARM,STATES


class DebugPage(QWidget):
    output_requested=Signal(object)
    command_requested=Signal(int)
    workspace_requested=Signal()
    connection_requested=Signal()

    def __init__(self):
        super().__init__()
        self.caps=None; self.connected=False; self.busy=False; self.status=None; self.initial=512; self.initials={}
        self.confirmed=None; self.pending=False; self.failure=None; self.channel_statuses={}
        outer=QVBoxLayout(self); outer.setContentsMargins(0,0,0,0)
        scroll=QScrollArea(); scroll.setWidgetResizable(True); outer.addWidget(scroll)
        content=QWidget(); layout=QVBoxLayout(content); layout.setContentsMargins(0,0,0,0); layout.setSpacing(16); scroll.setWidget(content)
        def label(text,name=None):
            w=QLabel(text); w.setWordWrap(True)
            if name:w.setObjectName(name)
            return w
        def card():
            frame=QFrame(); frame.setObjectName('card'); box=QVBoxLayout(frame); box.setContentsMargins(24,22,24,22); box.setSpacing(14); layout.addWidget(frame); return box
        def button(text,action,name=None):
            b=SoftButton(text); b.clicked.connect(action)
            if name:b.setObjectName(name)
            return b
        layout.addWidget(label('Debug · 静态输出','title'))
        layout.addWidget(label('选择一个 DAC，保持指定原码，用于检查接线、极性和输出幅度。','subtitle'))
        row=QHBoxLayout(); self.connection_label=label('未连接','subtitle'); row.addWidget(self.connection_label,1)
        row.addWidget(button('连接设置',lambda:self.connection_requested.emit()));layout.addLayout(row)
        self.hardware_label=label('','subtitle'); layout.addWidget(self.hardware_label)
        box=card(); box.addWidget(label('设置静态原码','section'))
        row=QHBoxLayout(); self.channel=QComboBox(); self.code=QSpinBox(); self.safe=QSpinBox()
        self.code.setRange(0,1023); self.safe.setRange(0,1023); self.code.setValue(512); self.safe.setValue(512)
        for title,field in [('输出通道',self.channel),('静态输出原码',self.code),('本次提交的中止码',self.safe)]:
            col=QVBoxLayout(); col.addWidget(label(title,'subtitle')); col.addWidget(field); row.addLayout(col)
        box.addLayout(row)
        row=QHBoxLayout()
        for title,value in [('填入最小码',lambda:0),('填入数字中点',lambda:1<<(self.caps.dac_bits-1)),('填入最大码',lambda:(1<<self.caps.dac_bits)-1)]:
            row.addWidget(button(title,lambda checked=False,f=value:self.code.setValue(f()) if self.caps else None))
        row.addStretch(); box.addLayout(row)
        self.route_label=label('','subtitle'); box.addWidget(self.route_label)
        self.validation=label('','subtitle'); box.addWidget(self.validation)
        row=QHBoxLayout()
        self.apply_button=button('应用静态输出',lambda:self.request_output(self.code.value()),'primary')
        self.initial_button=button('返回方案初始码',lambda:self.request_output(self.initial))
        self.midpoint_button=button('输出数字中点',lambda:self.request_output(1<<(self.caps.dac_bits-1)))
        for b in (self.apply_button,self.initial_button,self.midpoint_button):row.addWidget(b)
        box.addLayout(row)
        box.addWidget(label('修改输入框只更新待提交值；点击上方输出按钮后才发送。返回初始码使用该 DAC 所选的已保存方案；未选择方案时此按钮不可用。','subtitle'))
        box=card(); readback_frame=box.parentWidget();box.addWidget(label('设备回读','section'))
        self.readback=label('—','metric'); box.addWidget(self.readback)
        self.feedback=label('尚未提交静态输出','subtitle'); box.addWidget(self.feedback)
        self.abort_note=label('中止使用设备中已提交的中止码，不使用尚未应用的输入值。','subtitle'); box.addWidget(self.abort_note)
        row=QHBoxLayout(); self.disarm_button=button('取消布防',lambda:self.command_requested.emit(DISARM))
        self.abort_button=button('中止 · 使用已提交中止码',lambda:self.command_requested.emit(ABORT),'danger')
        row.addWidget(self.disarm_button); row.addWidget(self.abort_button); row.addStretch(); box.addLayout(row)
        box=card(); box.addWidget(label('输出行为','section'))
        self.behavior_label=label('• 一次提交一个所选 DAC 的静态值。并行固件中其他 DAC 保持原配置。旧固件中其他 DAC 保持数字中点。\n'
            '• 不需要触发，DAC 时钟按固件设置持续输出；切换页面或断开串口不会自动归零。\n'
            '• 应用静态输出会替换设备中的波形配置；在对应 DAC 页面重新下载后才能布防，本地方案不变。\n'
            '• 原码回读不是电压测量；数字中点并非经校准的零电压。','subtitle');box.addWidget(self.behavior_label)
        layout.removeWidget(readback_frame);outer.addWidget(readback_frame)
        layout.addStretch()
        self.channel.currentIndexChanged.connect(self.refresh)
        self.code.valueChanged.connect(self.refresh); self.safe.valueChanged.connect(self.refresh)

    def set_context(self,caps,connected,busy,status,demo,initial,channel_statuses=None):
        self.channel_statuses=channel_statuses or {}
        old_caps=self.caps
        self.caps=caps; self.connected=connected; self.busy=busy; self.status=status; self.initials=initial if isinstance(initial,dict) else {i:initial for i in range(caps.dac_count)}
        selected=self.channel.currentData()
        if selected is None:selected=0
        if old_caps is None or old_caps.dac_count!=caps.dac_count:
            self.channel.blockSignals(True); self.channel.clear()
            for index in range(caps.dac_count):self.channel.addItem(f'DAC {index+1}',index)
            if selected>=caps.dac_count:self.channel.addItem(f'DAC {selected+1} · 当前设备不可用',selected)
            self.channel.setCurrentIndex(self.channel.findData(selected)); self.channel.blockSignals(False)
        limit=(1<<caps.dac_bits)-1
        for field in (self.code,self.safe):
            field.blockSignals(True); field.setMaximum(max(limit,field.value())); field.blockSignals(False)
        self.connection_label.setText(('演示设备已连接' if demo else '硬件设备已连接') if connected else '未连接 · 请前往“板卡与连接”选择设备')
        self.hardware_label.setText(f'{caps.dac_bits} bit · 原码 0～{limit} · {caps.dac_count} 路 DAC · DAC 时钟 {caps.core_hz/caps.dac_divider/1e6:g} MHz')
        self.refresh()

    def request_output(self,code):
        self.output_requested.emit(dict(channel=self.channel.currentData(),code=code,safe=self.safe.value()))

    def refresh(self,*args):
        if self.caps is None:return
        limit=(1<<self.caps.dac_bits)-1; channel=self.channel.currentData();self.initial=self.initials.get(channel)
        channel_valid=type(channel) is int and 0<=channel<self.caps.dac_count
        supported=supports_sample_clock(self.caps)
        if self.caps.parallel_channels:self.status=self.channel_statuses.get(channel)
        state=self.status['state'] if self.status else None
        ready=self.connected and not self.busy and state in (0,3) and bool(self.status and self.status['locked']) and supported
        valid=channel_valid and self.safe.value()<=limit
        self.apply_button.setEnabled(ready and valid and self.code.value()<=limit)
        self.initial_button.setEnabled(ready and valid and self.initial is not None and 0<=self.initial<=limit)
        self.midpoint_button.setEnabled(ready and valid)
        self.disarm_button.setEnabled(self.connected and not self.busy and state==1)
        self.abort_button.setEnabled(self.connected and not self.busy)
        self.initial_button.setText(f'返回所选方案初始码 {self.initial}' if self.initial is not None else '请先为本 DAC 选择方案')
        self.route_label.setText(f'DAC {channel+1} → dac_data[{(channel+1)*self.caps.dac_bits-1}:{channel*self.caps.dac_bits}] / dac_clk[{channel}]；'+('其他通道保持原配置' if self.caps.parallel_channels else f'其他通道 = {1<<(self.caps.dac_bits-1)}') if channel_valid else '当前所选 DAC 不存在，请重新选择输出通道。')
        message=('原码越界：请按当前位数修改，软件不会自动缩放。' if self.code.value()>limit or self.safe.value()>limit else
                 '当前固件不支持此功能。' if not supported else
                 '请先中止或取消布防，再应用静态输出。' if state in (1,2) else
                 '待提交参数；尚未发送到设备。')
        self.validation.setText(message)
        if not self.connected or not self.status:self.readback.setText('—  等待设备状态')
        else:self.readback.setText(f'DAC {self.status["channel"]+1}   ·   {self.status["code"]}   ·   {STATES.get(state,"未知状态")}')
        if self.pending:self.feedback.setText('静态输出提交中，等待设备确认…')
        elif self.failure:self.feedback.setText(f'静态输出未确认：{self.failure}')
        elif self.confirmed:
            r=self.confirmed
            if self.status and self.status['channel']==r['channel'] and self.status['config_id']==r['config_id']:
                self.feedback.setText(f'本次静态配置已确认 · DAC {r["channel"]+1} · 设置码 {r["code"]} · 已提交中止码 {r["safe"]}；当前输出以上方回读为准。')
            else:self.feedback.setText('设备配置已变化；当前输出以上方回读为准。')
        else:self.feedback.setText('尚未提交静态输出；上方显示当前设备状态。')
