"""Saved-plan assignment and control; never reads the designer's draft."""
from dataclasses import replace
from PySide6.QtWidgets import QWidget,QVBoxLayout,QHBoxLayout,QLabel,QComboBox,QFrame,QScrollArea
from .appearance import SoftButton, Foldout, FieldGrid, fit_combo
from .model import compile_profile,fingerprint
from .output_plot import OutputWaveformPlot
from .output_actions import OutputActions
from .output_state import trigger_description
from .protocol import ARM,TRIGGER,ABORT,DISARM,STATES
from .time_input import display_time
from .boot import matches_boot
from .resource_view import ResourceUsageView


class ChannelPage(QWidget):
    def __init__(self,studio):
        super().__init__();self.studio=studio;self.channel=0;self.preview_key=None;self.pending=None
        outer=QVBoxLayout(self);outer.setContentsMargins(0,0,0,0);outer.setSpacing(12)
        header=QHBoxLayout();back=SoftButton('‹  通道总览');back.clicked.connect(lambda:studio.navigate(4));header.addWidget(back)
        self.title=QLabel();self.title.setObjectName('title');header.addWidget(self.title);header.addStretch()
        self.live_status=QLabel();self.live_status.setObjectName('badge');header.addWidget(self.live_status);outer.addLayout(header)
        self.scroll=QScrollArea();self.scroll.setWidgetResizable(True);outer.addWidget(self.scroll,1)
        content=QWidget();self.scroll.setWidget(content);layout=QVBoxLayout(content);layout.setContentsMargins(0,0,6,0);layout.setSpacing(12)
        frame=QFrame();frame.setObjectName('card');box=QVBoxLayout(frame);box.setContentsMargins(20,18,20,18);box.setSpacing(12);layout.addWidget(frame)
        row=QHBoxLayout();self.plot_title=QLabel();self.plot_title.setObjectName('section');self.plot_title.setWordWrap(True);row.addWidget(self.plot_title,1)
        self.view=fit_combo(QComboBox());self.view.setFixedWidth(150)
        for text,value in [('已加载波形','loaded'),('本地方案','local'),('版本对比','compare')]:self.view.addItem(text,value)
        self.view.currentIndexChanged.connect(self.refresh);row.addWidget(self.view);box.addLayout(row)
        self.plot=OutputWaveformPlot();box.addWidget(self.plot,1)
        self.plot_note=QLabel();self.plot_note.setWordWrap(True);self.plot_note.setObjectName('subtitle');box.addWidget(self.plot_note)
        self.resource_view=ResourceUsageView();box.addWidget(self.resource_view)
        self.settings_section=Foldout('方案与触发设置');layout.addWidget(self.settings_section);box=self.settings_section.body
        row=QHBoxLayout();row.addWidget(QLabel('本地方案'));self.plan=fit_combo(QComboBox());row.addWidget(self.plan,1)
        self.design=SoftButton('编辑方案');self.design.clicked.connect(self.edit_plan);row.addWidget(self.design);box.addLayout(row)
        self.details=QLabel();self.details.setWordWrap(True);box.addWidget(self.details)
        self.routing=fit_combo(QComboBox());self.routing.addItem('沿用方案触发设置','inherit');self.routing.addItem('为本 DAC 单独设置','override');box.addWidget(self.routing)
        self.trigger_fields=FieldGrid(3,170);box.addWidget(self.trigger_fields)
        self.source=QComboBox();self.source.addItem('外部触发','external');self.source.addItem('软件触发','software')
        self.input=QComboBox();self.edge=QComboBox();self.edge.addItem('上升沿','rising');self.edge.addItem('下降沿','falling')
        for label,field in [('触发方式',self.source),('物理输入',self.input),('触发边沿',self.edge)]:self.trigger_fields.add_field(label,field)
        self.route=QLabel();self.route.setWordWrap(True);box.addWidget(self.route)
        self.version_section=Foldout('加载版本信息');layout.addWidget(self.version_section);box=self.version_section.body
        self.version=QLabel();self.version.setWordWrap(True);box.addWidget(self.version)
        self.state=QLabel();self.state.setObjectName('section');box.addWidget(self.state)
        self.status=QLabel();self.status.setWordWrap(True);box.addWidget(self.status)
        self.version_names=QLabel();self.version_names.setWordWrap(True);box.addWidget(self.version_names)
        self.actions=OutputActions(self.act,studio.parallel_page.abort_all);outer.addWidget(self.actions)
        self.buttons=self.actions.buttons;self.feedback=self.actions.feedback
        layout.addStretch()
        self.plan.currentIndexChanged.connect(self.choose)
        self.routing.currentIndexChanged.connect(self.change_route)
        for field in (self.source,self.input,self.edge):field.currentIndexChanged.connect(self.change_route)

    def change_route(self):
        p=self.profile()
        if not p:return
        values={} if self.routing.currentData()=='inherit' else dict(trigger=self.source.currentData(),trigger_input=self.input.currentData(),trigger_edge=self.edge.currentData())
        if values and p.timing_mode=='gate':values.update(trigger='external',trigger_edge='rising')
        self.studio.parallel_page.store.override(self.channel,values)
        self.actions.message('本通道设置已保存；点击准备后应用。共享方案保持原有设置。')
        self.studio.parallel_page.refresh();self.refresh()

    def refresh_route(self,p):
        fields=(self.routing,self.source,self.input,self.edge)
        for field in fields:field.blockSignals(True)
        try:
            source=p.trigger if p else 'external';index=p.trigger_input if p else 0
            self.input.clear()
            for i in range(self.studio.caps.trigger_count):self.input.addItem(f'输入 {i+1}',i)
            if index>=self.studio.caps.trigger_count:self.input.addItem(f'输入 {index+1} · 当前不可用',index)
            self.input.setCurrentIndex(self.input.findData(index));self.source.setCurrentIndex(self.source.findData(source));self.edge.setCurrentIndex(self.edge.findData(p.trigger_edge if p else 'rising'))
            override=bool(self.studio.parallel_page.store.assignment(self.channel).get('trigger_override'))
            self.routing.setCurrentIndex(int(override));events=bool(p and p.mode=='sequence' and p.sequence_mode=='events')
            self.routing.setEnabled(bool(p) and not events and not self.studio.busy)
            self.trigger_fields.setVisible(bool(p) and not events)
            enabled=override and not self.studio.busy
            self.source.setEnabled(enabled and p.timing_mode!='gate' if p else False)
            self.input.setEnabled(enabled and source=='external');self.edge.setEnabled(enabled and source=='external' and p.timing_mode!='gate' if p else False)
        finally:
            for field in fields:field.blockSignals(False)

    def set_channel(self,ch):
        self.channel=ch;self.actions.message('');self.settings_section.toggle.setChecked(False);self.version_section.toggle.setChecked(False);self.view.setCurrentIndex(0);self.scroll.verticalScrollBar().setValue(0);self.refresh_library()

    def resizeEvent(self,event):
        if hasattr(self,'plot'):self.plot.setMinimumHeight(max(250,min(430,self.height()-520)))
        super().resizeEvent(event)

    def profile(self):
        return self.studio.parallel_page.store.resolve(self.channel)

    def refresh_library(self):
        pool=self.studio.parallel_page
        key=pool.rows[self.channel]['combo'].currentData() if self.channel<len(pool.rows) else None
        self.plan.blockSignals(True);self.plan.clear();self.plan.addItem('请选择已保存方案',None)
        for p in pool.profiles:self.plan.addItem(f'{p.name} · {p.dac_bits} bit',p.id)
        self.plan.setCurrentIndex(max(0,self.plan.findData(key)));self.plan.blockSignals(False);self.refresh()

    def choose(self):
        pool=self.studio.parallel_page
        if self.channel<len(pool.rows):
            combo=pool.rows[self.channel]['combo'];combo.setCurrentIndex(max(0,combo.findData(self.plan.currentData())))
        self.actions.message('本地方案已选择；点击准备后应用到本 DAC。');self.refresh()

    def edit_plan(self):
        s=self.studio;p=self.profile()
        if p and p.mode=='scan':
            original=next(item for item in s.parallel_page.profiles if item.id==p.id)
            if not s.scan_page.open_profile(original):return
            s.designer_tabs.setCurrentIndex(2);s.navigate(0);return
        if p and p.mode=="sequence":
            original=next(item for item in s.parallel_page.profiles if item.id==p.id)
            if not s.sequence_page.open_profile(original):return
            s.designer_tabs.setCurrentIndex(1);s.navigate(0);return
        s.designer_tabs.setCurrentIndex(0)
        if p and p.id!=s.profile.id:
            if not s.maybe_discard():return
            original=next(p for p in s.parallel_page.profiles if p.id==self.plan.currentData())
            s.load_profile(original)
        s.navigate(0)

    def refresh(self):
        s=self.studio;ch=self.channel;p=self.profile();caps=s.caps
        usage=s.resource_usage(f'channel:{ch}',p);resource_ok=usage is None or usage.fits
        self.resource_view.set_report(usage,'本地方案 · '+s.resource_source(),p is not None)
        self.title.setText(f'DAC {ch+1}')
        status=s.channel_statuses.get(ch) if caps.parallel_channels else s.last_status
        if status and status.get('channel')!=ch:status=None
        known=s.connected and status is not None
        idle=known and status['state'] in (0,3)
        ready=s.connected and not s.busy and ch<caps.dac_count
        valid=False;error='请选择一个已保存方案。'
        if p:
            try:compile_profile(p,s.resource_caps());valid=True
            except ValueError as exc:error=str(exc)
            fmt=lambda value:'%g %s'%display_time(value)
            shape='三段函数' if p.mode=='function' else 'Ramp'
            timing=f'进入 {fmt(p.rise_ms)} / 高电平保持 / 返回 {fmt(p.fall_ms)}' if p.timing_mode=='gate' else f'进入 {fmt(p.rise_ms)} / 保持 {fmt(p.hold_ms)} / 返回 {fmt(p.fall_ms)}'
            if p.mode=='function' and p.function_layout=='single':timing=f'整段函数 {fmt(p.function_ms)}'
            self.details.setText(f'{shape} · {p.dac_bits} bit · 初始 {p.initial} → 目标 {p.amplitude} → 初始 {p.initial} · 中止 {p.safe}\n{timing}')
            if p.mode=='function' and p.function_layout=='single':
                self.details.setText(f'整段函数 · {p.dac_bits} bit · 初始/结束 {p.initial} · 中止 {p.safe}\n{timing} · 输出按保存的表达式计算')
            if p.mode in ('ramp','function') and p.timing_mode=='fixed':
                self.details.setText(self.details.text()+(' · ∞ 无限循环，直到中止' if p.repeat==0 else f' · 重复 {p.repeat} 次'))
            if p.mode=="sequence":
                repeat='每轮重新等待事件' if p.sequence_mode=='events' else '∞ 无限重复，直到中止' if p.repeat==0 else f'整表重复 {p.repeat} 次'
                self.details.setText(f'{"固定时间表" if p.sequence_mode=="timeline" else "事件序列"} · {len(p.sequence_steps)} 步 · {p.dac_bits} bit · 初始/等待 {p.initial} · 中止 {p.safe}\n{repeat}')
            if p.mode=='scan':
                last=p.scan_start+(p.scan_points-1)*p.scan_step
                self.details.setText(f'触发步进 · 起始 {p.scan_start} · 步长 {p.scan_step:+d} · {p.scan_points:,} 点 · 末点 {last}\n等待码 {p.initial} · 中止码 {p.safe} · '+('末点保持并结束' if p.scan_end=='hold' else '下一次触发从头循环'))
        else:self.details.setText('先在“方案设计与保存”中新建并保存波形，然后为本通道选择方案。')
        if p:
            from .sequences import has_interpolation
            if has_interpolation(p):self.details.setText(self.details.text()+'\n分段线性插值 · 精度与完整检查结果见下方容量栏')
        route=f'dac_data[{ch*caps.dac_bits+caps.dac_bits-1}:{ch*caps.dac_bits}] / dac_clk[{ch}]'
        if p and not (p.mode=='sequence' and p.sequence_mode=='events'):
            route+=f' · 输入 {p.trigger_input+1} · '+('上升进入 / 下降返回' if p.timing_mode=='gate' else ('上升沿' if p.trigger_edge=='rising' else '下降沿')) if p.trigger=='external' else ' · 软件触发'
        self.route.setText(route)
        events=bool(p and p.mode=="sequence" and p.sequence_mode=="events")
        if events:
            route+=" · 每步独立触发输入（可共用）";self.route.setText(route)
        identity=s.parallel_page.identities.get(ch)
        boot_match=bool(valid and known and matches_boot(p,status,caps))
        matches=bool(valid and known and s.parallel_page.store.matches(p,status,caps))
        self.version.setText(error if not valid else '所选保存版本已下载并确认。' if matches else '所选保存版本尚未确认下载；选择或修改方案不会自动改变硬件输出。')
        if boot_match:self.version.setText('设备正在使用与所选方案一致的上电固化版本，无需重新下载。')
        if s.connected and p and p.mode!='scan' and p.trigger=='external' and p.timing_mode=='fixed' and not events:
            self.route.setText(route+(' · 一次触发后持续循环，点击中止停止' if p.repeat==0 else ' · 完成后自动重新布防' if caps.fixed_auto_rearm else ' · 此固件完成后需手动布防'))
        self.state.setText(f'DAC {ch+1} · {STATES.get(status["state"],"未知")}' if known else '等待连接或回读 · 设备连接入口在左侧“板卡与连接”')
        self.live_status.setText(f'{STATES.get(status["state"],"未知")} · 原码 {status["code"]}' if known else '等待回读' if s.connected else '未连接')
        if known and status.get('scan_mode'):
            self.live_status.setText(f'{STATES.get(status["state"],"未知")} · 第 {status["scan_position"]} 点 · 原码 {status["code"]}')
        loops='65535+' if known and status['loops']==65535 else str(status['loops']) if known else ''
        self.status.setText(f'当前原码 {status["code"]} · 完成 {loops} 次 · 设备配置 {status["config_id"]:08X}' if known else '—')
        if known and status.get('infinite_repeat'):
            self.status.setText(self.status.text()+' · ∞ 无限重复'+('进行中' if status['state']==2 else '已设置'))
        if known and status.get('scan_mode'):
            self.status.setText(f'当前原码 {status["code"]} · 本轮已输出 {status["scan_position"]} 点 · 完成 {loops} 轮 · '+('循环步进' if status['scan_loop'] else '末点保持')+f' · 配置 {status["config_id"]:08X}')
        if known and status.get("sequence_mode") and status["state"]!=0:
            self.status.setText(self.status.text()+f' · 步骤 {status["sequence_step"]+1} · 输入 {status["trigger_input"]+1} · '+{0:"预取中",1:"等待边沿",2:"延时中",3:"播放中"}.get(status["sequence_phase"],"准备中"))
        self.plan.setEnabled(not s.busy);self.design.setEnabled(not s.busy)
        self.refresh_route(p)
        self.actions.scope.setText(f'仅 DAC {ch+1}')
        self.buttons['prepare'].setText(f'准备 DAC {ch+1}')
        self.buttons['abort'].setText('中止本通道')
        legacy_idle=not caps.parallel_channels and s.last_status and s.last_status['state'] in (0,3)
        self.buttons['prepare'].setEnabled(bool(ready and valid and resource_ok and (idle or legacy_idle or matches and status['state']==1)))
        self.actions.all_action.setEnabled(s.connected and not s.busy)
        loaded=s.parallel_page.store.loaded(ch,status if known else None,caps,s.board_page.flash_page.boot_profiles)
        trigger=trigger_description(loaded)
        self.actions.hint.setText(trigger if known and loaded else '准备：检查方案 → 按需下载 → 布防。仅操作当前 DAC。')
        if p and not matches:self.actions.hint.setText('本地方案待准备 · 设备继续使用已加载版本')
        if p and not matches and known and status['state'] in (1,2):self.actions.hint.setText('设备正在使用旧版本 · 请先中止或取消布防，再准备本地方案')
        if not s.connected:self.actions.hint.setText('请先在“板卡与连接”连接设备')
        if not resource_ok:self.actions.hint.setText('本地方案无法下发：'+usage.error)

        self.buttons['upload'].setEnabled(bool(ready and valid and resource_ok and (idle or (not caps.parallel_channels and s.last_status and s.last_status['state'] in (0,3)))))
        self.buttons['arm'].setEnabled(ready and matches and idle)
        self.buttons['trigger'].setEnabled(ready and matches and status['state']==1 and p.trigger=='software' and p.timing_mode!='gate')
        self.buttons['trigger'].setVisible(bool(p and p.trigger=='software' and p.timing_mode!='gate' and not events))
        self.buttons['disarm'].setEnabled(ready and known and status['state']==1)
        self.buttons['abort'].setEnabled(ready and known)
        self.buttons['external'].setVisible(bool(s.connected and s.demo and p and p.trigger=='external'))
        self.buttons['falling'].setVisible(bool(s.connected and s.demo and p and (p.timing_mode=='gate' or events and known and status.get('gate_mode'))))
        self.buttons['external'].setText('模拟上升沿' if p and p.timing_mode=='gate' else '模拟所选触发')
        for key in ('external','falling'):self.buttons[key].setToolTip('模拟物理输入；等待同一输入的其他通道也会响应')
        self.buttons['external'].setEnabled(ready and s.demo and matches and status['state']==1 and p.trigger=='external')
        self.buttons['falling'].setEnabled(ready and s.demo and matches and status['state']==2 and (p.timing_mode=='gate' or events and status.get('gate_mode')))
        for name in ('external','falling'):
            # Demo actions live in the menu; visibility is independent of the hidden adapter buttons.
            available=bool(s.connected and s.demo and p and p.trigger=='external' and (name=='external' or p.timing_mode=='gate' or events))
            self.buttons[name].setProperty('available',available);self.buttons[name].hide()
        self.version_names.setText('已加载：'+(loaded.name if loaded else '未找到与设备配置一致的本地快照')+'\n本地方案：'+(p.name if p else '未选择'))
        mode=self.view.currentData()
        self.plot_title.setText(('本地方案' if mode=='local' else '版本对比' if mode=='compare' else '已加载波形')+' · '+(p.name if mode=='local' and p else loaded.name if loaded else '待确认'))
        empty='设备配置已存在，但本机没有匹配的波形快照' if known and status.get('valid') else '请先准备本通道方案，确认后显示已加载波形'
        if not known:empty='连接设备并回读配置后显示已加载波形；可切换查看本地方案'
        self.plot.set_profiles(loaded,p,caps,mode,empty)
        self.plot_note.setText('蓝色实线：已加载 · 灰色虚线：本地方案 · 200 MHz / 5 ns，预览抽样显示，非模拟电压实测' if mode=='compare' else '本地保存版本 · 灰色虚线 · 准备后应用，200 MHz / 5 ns' if mode=='local' else '设备配置已匹配 · 蓝色实线 · 200 MHz / 5 ns，预览抽样显示，非模拟电压实测' if loaded else empty)
        self.plot_note.setText(self.plot_note.text().replace('200 MHz / 5 ns',f'{caps.core_hz/1e6:g} MSPS / {1e9/caps.core_hz:g} ns'))
        shown=p if mode=='local' else loaded
        if shown and shown.repeat==0:self.plot_note.setText(self.plot_note.text()+' · 显示单轮，∞ 无限循环')
        if (p if mode=='local' else loaded) and (p if mode=='local' else loaded).mode=='scan':
            self.plot_note.setText('横轴：触发次数 · 两次触发之间保持当前码 · 第 1 次输出起始码 · DAC 时钟持续输出')


    def act(self,key):
        self.refresh()
        if not self.buttons[key].isEnabled():return
        s=self.studio;p=self.profile();ch=self.channel
        if key=='prepare':
            self.pending=('prepare',ch);self.actions.message(f'正在准备 DAC {ch+1}…')
            s.parallel_page.prepare_channels([ch]);return
        self.pending=('upload' if key=='upload' else 'external' if key in ('external','falling') else 'command',ch)
        if key=='upload':
            s.uploading_profile=p;s.channel_uploads.pop(ch,None);s.parallel_page.identities.pop(ch,None)
            s.send('upload',p)
        elif key in ('external','falling'):
            status=s.channel_statuses.get(ch,s.last_status) or {}
            source=status.get('trigger_input',p.trigger_input) if p.mode=='sequence' and p.sequence_mode=='events' else p.trigger_input
            edge=status.get('trigger_edge',p.trigger_edge) if p.mode=='sequence' and p.sequence_mode=='events' else p.trigger_edge
            s.send('external',(source,'falling' if key=='falling' else edge))
        else:
            cmd={'arm':ARM,'trigger':TRIGGER,'disarm':DISARM,'abort':ABORT}[key]
            s.send('command',dict(command=cmd,channel=ch) if s.caps.parallel_channels else cmd)
        self.actions.message(f'DAC {ch+1}：命令已发送，等待设备确认。')

    def finish(self,operation,error=None):
        if not self.pending or self.pending[0]!=operation:return
        _,ch=self.pending;self.pending=None
        self.actions.message(f'DAC {ch+1}：'+('操作未完成：'+error if error else '设备已确认，输出状态以上方回读为准。'))
