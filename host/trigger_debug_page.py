"""Real FPGA virtual input control; no implicit enable on connection or restore."""
from PySide6.QtCore import Signal
from PySide6.QtWidgets import QWidget,QVBoxLayout,QHBoxLayout,QLabel,QComboBox,QSpinBox,QFrame
from .appearance import SoftButton
from .time_input import TimeInput


class TriggerDebugPage(QWidget):
    requested=Signal(object)

    def __init__(self):
        super().__init__();self.caps=None;self.connected=False;self.busy=False;self.value={}
        layout=QVBoxLayout(self);layout.setContentsMargins(24,24,24,24);layout.setSpacing(18)
        title=QLabel('触发调试');title.setObjectName('title');layout.addWidget(title)
        note=QLabel('将指定触发输入临时改为软件控制。使用这个输入的 DAC 同时接收边沿，原方案仍选择“外部触发”。\n'
                    '加载自己的方案并布防 → 在此直接设高/低或发送脉冲。首次发送自动接管输入并恢复布防，不替换波形。');note.setWordWrap(True);layout.addWidget(note)
        row=QHBoxLayout();row.addWidget(QLabel('触发输入'));self.source=QComboBox();row.addWidget(self.source,1)
        self.enable=SoftButton('接管所选输入 · 保留布防');self.enable.clicked.connect(lambda:self.send('enable'));row.addWidget(self.enable)
        self.enable_all=SoftButton('接管全部输入');self.enable_all.clicked.connect(lambda:self.send('enable_all'));row.addWidget(self.enable_all);layout.addLayout(row)
        card=QFrame();card.setObjectName('card');box=QVBoxLayout(card);box.setContentsMargins(20,20,20,20);box.setSpacing(16);layout.addWidget(card)
        row=QHBoxLayout();self.actions=[]
        for text,op in [('设为高电平','high'),('设为低电平','low')]:
            b=SoftButton(text);b.clicked.connect(lambda checked=False,o=op:self.send(o));row.addWidget(b);self.actions.append(b)
        box.addLayout(row)
        row=QHBoxLayout();self.high=TimeInput();self.period=TimeInput();self.high.setValue(10);self.period.setValue(100)
        for title,field in [('高电平时间',self.high),('脉冲周期',self.period)]:
            col=QVBoxLayout();col.addWidget(QLabel(title));col.addWidget(field);row.addLayout(col)
        box.addLayout(row)
        row=QHBoxLayout();row.addWidget(QLabel('脉冲次数'));self.count=QSpinBox();self.count.setRange(0,1000000000);self.count.setValue(1);self.count.setSpecialValueText('无限循环');row.addWidget(self.count)
        for text,op in [('发送单脉冲','pulse'),('开始周期脉冲','periodic')]:
            b=SoftButton(text);b.clicked.connect(lambda checked=False,o=op:self.send(o));row.addWidget(b);self.actions.append(b)
        box.addLayout(row)
        self.feedback=QLabel('尚未连接支持触发调试的固件');self.feedback.setWordWrap(True);layout.addWidget(self.feedback)
        self.outputs=QLabel('DAC 状态待回读');self.outputs.setWordWrap(True);layout.addWidget(self.outputs)
        self.timing=QLabel();self.timing.setWordWrap(True);layout.addWidget(self.timing)
        self.stop=SoftButton('结束调试 · 中止全部 DAC 并恢复物理触发');self.stop.setObjectName('danger');self.stop.clicked.connect(lambda:self.send('stop'));layout.addWidget(self.stop)
        note=QLabel('脉冲由 FPGA 按核心时钟产生。设备回报的是已产生边沿数，不能视为已执行波形数；忙时触发仍按原规则处理。\n'
                    '首次接管会短暂输出中止码，再恢复原先等待触发的通道；扫描/事件序列进度从头开始。正在播放时不能切换来源。\n'
                    '调试期间软件保持心跳，失联约 3 秒后 FPGA 中止全部 DAC 并恢复物理触发。\n'
                    '调试接管状态不保存；结束调试会取消布防，需要重新布防才能使用物理触发。');note.setWordWrap(True);layout.addWidget(note);layout.addStretch()
        self.source.currentIndexChanged.connect(self.refresh)
        self.high.valueChanged.connect(self.refresh);self.period.valueChanged.connect(self.refresh)

    def send(self,operation):
        params=dict(operation=operation,input_index=self.source.currentData() or 0)
        if operation in ('pulse','periodic'):
            params.update(high_ticks=round(self.high.value()/1000*self.caps.core_hz),
                          period_ticks=round(self.period.value()/1000*self.caps.core_hz),count=self.count.value())
        self.requested.emit(params)

    def set_context(self,caps,connected,busy,statuses=None):
        self.caps=caps;self.connected=connected;self.busy=busy
        self.statuses=statuses or {}
        if self.source.count()!=caps.trigger_count:
            self.source.clear()
            for i in range(caps.trigger_count):self.source.addItem(f'输入 {i+1}',i)
        if not connected:self.value={}
        from .protocol import STATES
        self.outputs.setText('\n'.join(f'DAC {i+1} · {STATES.get(s["state"],"未知")} · 原码 {s["code"]} · 完成 {s["loops"]} · 忽略触发 {s["ignored"]}' for i,s in (statuses or {}).items()) or 'DAC 状态待回读')
        self.refresh()

    def readback(self,value):
        self.value=value;self.refresh()

    def refresh(self):
        if self.caps:
            hz=self.caps.core_hz;high=round(self.high.value()/1000*hz);period=round(self.period.value()/1000*hz)
            self.timing.setText(f'核心时钟 {hz/1e6:g} MHz · {1e9/hz:g} ns/拍 · 高电平 {high:,} 拍 · 周期 {period:,} 拍（按最近整数拍量化）')
        capable=bool(self.connected and self.caps and self.caps.trigger_debug)
        active=bool(self.value.get('mask',0)&(1<<(self.source.currentData() or 0))) and self.value.get('owned',True)
        running=self.value.get('running',False)
        self.enable.setEnabled(capable and not self.busy and not running)
        self.enable_all.setEnabled(self.enable.isEnabled())
        playing=any(s.get('state')==2 for s in getattr(self,'statuses',{}).values())
        available=active or (not playing and (not self.value.get('mask') or self.value.get('owned',False)))
        for b in self.actions:
            b.setEnabled(capable and available and not self.busy and not running)
            b.setToolTip('发送至当前已加载波形；首次发送自动接管输入并恢复原先的布防' if not active else '发送至使用此输入的已布防 DAC')
        self.stop.setEnabled(capable and not self.busy)
        if not capable:
            self.feedback.setText('请连接支持触发调试的新版并行固件。旧固件和当前演示设备不执行此功能。')
        else:
            mask=self.value.get('mask',0);levels=self.value.get('levels',0)
            routes='；'.join(f'输入 {i+1}：'+('软件高' if levels&(1<<i) else '软件低') if mask&(1<<i) else f'输入 {i+1}：物理' for i in range(self.caps.trigger_count))
            hint='' if active else ('\n有通道正在播放，请先停止后再切换软件触发。' if playing else '\n可直接发送：自动接管并恢复已布防通道；未布防的通道仍不会播放。')
            self.feedback.setText(f'{routes}\n已产生上升沿 {self.value.get("emitted",0)} 次 · '+('脉冲进行中' if running else '脉冲未运行')+(' · 心跳超时，已请求中止' if self.value.get('expired') else '')+hint)
