"""Output overview: compact rows, confirmed thumbnails and scoped batch actions."""
import copy
from dataclasses import replace
from PySide6.QtCore import Signal
from PySide6.QtWidgets import QWidget,QVBoxLayout,QHBoxLayout,QLabel,QFrame,QComboBox,QCheckBox,QScrollArea,QSizePolicy
from .appearance import SoftButton,fit_combo
from .model import fingerprint,compile_profile
from .protocol import ARM,TRIGGER,ABORT,STATES
from .output_state import OutputState,trigger_description
from .output_plot import OutputWaveformPlot
from .output_actions import OutputActions
from .resource_view import usage_text,usage_color


class ParallelPage(QWidget):
    operation=Signal(str,object)
    debug_requested=Signal(int)
    channel_requested=Signal(int)

    def __init__(self,library):
        super().__init__();self.library=library;self.store=OutputState(library)
        self.rows=[];self.states={};self.caps=None;self.connected=False;self.busy=False;self.boot_profiles={}
        self.resource_provider=None;self.resource_source=None
        self.path=self.store.path;self.saved=self.store.assignments;self.profiles=self.store.profiles
        outer=QVBoxLayout(self);outer.setContentsMargins(0,0,0,0);outer.setSpacing(12)
        title=QLabel('输出控制');title.setObjectName('title');outer.addWidget(title)
        self.note=QLabel('勾选通道进行批量操作；点击 DAC 或缩略图查看单路波形与设置。');self.note.setWordWrap(True);outer.addWidget(self.note)
        header=QHBoxLayout();self.selection_label=QLabel();header.addWidget(self.selection_label);header.addStretch()
        for title,checked in [('全选',True),('清除选择',False)]:
            b=SoftButton(title);b.clicked.connect(lambda value=False,v=checked:self.select_all(v));header.addWidget(b)
        outer.addLayout(header)
        self.scroll=QScrollArea();self.scroll.setWidgetResizable(True);outer.addWidget(self.scroll,1)
        content=QWidget();self.scroll.setWidget(content);self.grid=QVBoxLayout(content);self.grid.setContentsMargins(0,0,6,0);self.grid.setSpacing(10)
        self.actions=OutputActions(self.act,self.abort_all);outer.addWidget(self.actions)
        self.download=self.actions.buttons['upload'];self.arm=self.actions.buttons['arm'];self.trigger=self.actions.buttons['trigger'];self.abort=self.actions.buttons['abort'];self.prepare=self.actions.buttons['prepare']
        self.summary=self.actions.hint;self.feedback=self.actions.feedback
        self.feedback.setText('');self.actions.buttons['disarm'].setProperty('available',False)
        self.actions.buttons['external'].setProperty('available',False);self.actions.buttons['falling'].setProperty('available',False)

    @property
    def identities(self):return self.store.identities
    @identities.setter
    def identities(self,value):self.store.identities=value

    def selected(self):return [i for i,r in enumerate(self.rows) if r['check'].isChecked()]

    def select_all(self,value):
        if self.busy:return
        for r in self.rows:r['check'].setChecked(value)

    def rebuild(self,count,bits):
        for row in self.rows:
            row['frame'].hide();row['frame'].deleteLater()
        while self.grid.count():self.grid.takeAt(0)
        self.rows=[]
        for i in range(count):
            f=QFrame();f.setObjectName('card');row=QHBoxLayout(f);row.setContentsMargins(16,14,16,14);row.setSpacing(14)
            check=QCheckBox();check.setToolTip(f'将 DAC {i+1} 加入本次操作范围');check.setChecked(self.saved.get(str(i),{}).get('enabled',True));row.addWidget(check)
            channel=SoftButton(f'DAC {i+1}');channel.setFixedWidth(80);row.addWidget(channel)
            texts=QVBoxLayout();name=QLabel();name.setObjectName('section');name.setWordWrap(True);name.setSizePolicy(QSizePolicy.Ignored,QSizePolicy.Preferred)
            detail=QLabel();detail.setWordWrap(True);detail.setObjectName('subtitle');detail.setSizePolicy(QSizePolicy.Ignored,QSizePolicy.Preferred)
            resource=QLabel();resource.setWordWrap(True);resource.setSizePolicy(QSizePolicy.Ignored,QSizePolicy.Preferred)
            texts.addWidget(name);texts.addWidget(detail);texts.addWidget(resource);row.addLayout(texts,1)
            plot=OutputWaveformPlot(True);row.addWidget(plot)
            status=QLabel();status.setFixedWidth(95);status.setWordWrap(True);row.addWidget(status)
            # Compatibility adapters for existing saved assignments and automation.
            # Persistent assignments and confirmed profiles live in OutputState.
            combo=fit_combo(QComboBox(f));combo.hide();debug=SoftButton('静态调试',f);debug.hide()
            self.rows.append(dict(frame=f,check=check,combo=combo,status=status,detail=detail,name=name,resource=resource,plot=plot,channel=channel,debug=debug))
            self.grid.addWidget(f)
            check.toggled.connect(self.save_selection);combo.currentIndexChanged.connect(self.save_selection)
            channel.clicked.connect(lambda checked=False,c=i:self.channel_requested.emit(c));plot.clicked.connect(lambda c=i:self.channel_requested.emit(c))
            debug.clicked.connect(lambda checked=False,c=i:self.debug_requested.emit(c))
        self.grid.addStretch();self.refresh_library()

    def refresh_library(self):
        self.store.refresh_library();self.profiles=self.store.profiles
        for i,row in enumerate(self.rows):
            combo=row['combo'];key=self.store.assignment(i).get('profile')
            combo.blockSignals(True);combo.clear();combo.addItem('请选择已保存方案',None)
            for p in self.profiles:combo.addItem(p.name,p.id)
            combo.setCurrentIndex(max(0,combo.findData(key)));combo.blockSignals(False)
        self.refresh()

    def save_selection(self,*args):
        for i,r in enumerate(self.rows):self.store.assign(i,r['combo'].currentData(),r['check'].isChecked())
        self.store.save();self.refresh()

    def selected_profiles(self,channels=None):
        result=[]
        for i in self.selected() if channels is None else channels:
            p=self.store.resolve(i)
            if p is None:raise ValueError(f'请为 DAC {i+1} 选择已保存方案')
            result.append(p)
        return result

    def download_selected(self):
        try:
            profiles=self.selected_profiles()
            if not profiles:raise ValueError('请至少勾选一路')
            self.pending_profiles=copy.deepcopy(profiles);self.actions.message('正在检查并下载所选方案…');self.operation.emit('upload_many',profiles)
        except ValueError as exc:self.actions.message(str(exc))

    def prepare_channels(self,channels):
        try:
            profiles=self.selected_profiles(channels)
            if not profiles:raise ValueError('请至少选择一路')
            confirmed={p.channel:(self.states[p.channel]['config_id'],fingerprint(p)) for p in profiles if self.store.matches(p,self.states.get(p.channel),self.caps)}
            self.operation.emit('prepare',dict(profiles=copy.deepcopy(profiles),confirmed=confirmed))
        except (ValueError,KeyError) as exc:self.actions.message(str(exc))

    def act(self,key):
        if not self.actions.buttons[key].isEnabled():return
        if key=='prepare':self.prepare_channels(self.selected())
        elif key=='upload':self.download_selected()
        else:self.group({'arm':ARM,'trigger':TRIGGER,'abort':ABORT}[key])

    def group(self,command):
        channels=self.selected()
        if self.caps.parallel_channels:self.operation.emit('group',dict(command=command,channels=channels))
        elif len(channels)==1:self.operation.emit('command',command)

    def abort_all(self):
        if not self.connected or self.busy:return
        if self.caps.parallel_channels:self.operation.emit('group',dict(command=ABORT,channels=list(range(self.caps.dac_count))))
        else:self.operation.emit('command',ABORT)

    def loaded(self,ch):return self.store.loaded(ch,self.states.get(ch) if self.connected else None,self.caps,self.boot_profiles)

    def result(self,operation,result):
        if operation=='upload_many':self.actions.message('所选方案已下载并回读确认，可执行布防。')
        elif operation=='prepare':self.actions.message(f'准备完成 · 更新 {result["uploaded"]} 路，复用 {result["reused"]} 路 · 设备已确认布防命令')
        elif operation=='static':
            self.identities.pop(result['channel'],None);self.actions.message(f'DAC {result["channel"]+1} 已切换为静态码 {result["code"]}。')
        elif operation=='group':self.actions.message('所选通道命令已确认。')
        self.refresh()

    def set_context(self,caps,connected,busy,states):
        rebuild=self.caps is None or self.caps.dac_count!=caps.dac_count or self.caps.dac_bits!=caps.dac_bits
        self.caps=caps;self.connected=connected;self.busy=busy;self.states=states
        if rebuild:self.rebuild(caps.dac_count,caps.dac_bits)
        self.refresh()

    def refresh(self):
        if self.caps is None:return
        selected=self.selected();states=[self.states.get(i,{}) for i in selected]
        known=bool(selected) and all(s for s in states)
        ready=self.connected and not self.busy and (self.caps.parallel_channels or len(selected)==1 and known)
        self.selection_label.setText(f'{self.caps.dac_count} 路 DAC · 缩略图为已确认加载版本')
        self.actions.scope.setText(f'已选 {len(selected)} 路')
        self.actions.scope.setToolTip('、'.join(f'DAC {i+1}' for i in selected))
        error=''
        try:
            profiles=self.selected_profiles()
            for p in profiles:compile_profile(p,self.caps if self.connected else replace(self.caps,infinite_repeat=True))
        except ValueError as exc:profiles=[];error=str(exc)
        reports=[self.resource_provider(f'channel:{p.channel}',p) for p in profiles] if self.resource_provider else []
        failures=[r for r in reports if r is not None and not r.fits]
        if failures:error='本地方案无法下发：'+'；'.join(f'DAC {r.channel+1} · {r.error}' for r in failures)
        matches=len(profiles)==len(selected) and all(self.store.matches(p,self.states.get(p.channel),self.caps) for p in profiles)
        idle=known and all(s['state'] in (0,3) for s in states)
        self.download.setEnabled(ready and idle and bool(profiles) and not failures)
        self.arm.setEnabled(ready and idle and bool(profiles) and matches)
        self.prepare.setEnabled(ready and known and bool(profiles) and not failures and all(s['state'] in (0,3) or s['state']==1 and self.store.matches(p,s,self.caps) for p,s in zip(profiles,states)))
        software=bool(profiles) and all(p.trigger=='software' and p.timing_mode!='gate' and not(p.mode=='sequence' and p.sequence_mode=='events') for p in profiles)
        self.trigger.setVisible(software);self.trigger.setEnabled(ready and known and matches and software and all(s['state']==1 for s in states))
        self.abort.setEnabled(self.connected and not self.busy and bool(selected) and (self.caps.parallel_channels or len(selected)==1 and known))
        self.actions.all_action.setEnabled(self.connected and not self.busy)
        hint='准备：检查方案 → 按需下载 → 布防。选择通道不会开启或关闭输出。'
        if not selected:hint='请勾选本次要操作的 DAC。'
        elif error:hint=error
        elif not self.connected:hint='请先在“板卡与连接”连接设备；可点击 DAC 设置本地方案。'
        elif not self.caps.parallel_channels and len(selected)>1:hint='当前固件只支持单路操作，请进入对应 DAC 页面。'
        elif not software:hint='准备后等待外部输入；触发来源以各路已加载版本为准。'
        self.summary.setText(hint)
        for i,row in enumerate(self.rows):
            s=self.states.get(i) if self.connected else None;loaded=self.loaded(i);local=self.store.resolve(i)
            usage=self.resource_provider(f'channel:{i}',local) if self.resource_provider else None
            source=self.resource_source() if self.resource_source else '连接后检查资源'
            row['resource'].setText(usage_text(usage,'本地方案 · '+source,local is not None))
            row['resource'].setStyleSheet(f'color:{usage_color(usage)};font-size:11px;')
            row['combo'].setEnabled(not self.busy);row['check'].setEnabled(not self.busy);row['debug'].setEnabled(not self.busy)
            row['name'].setText(loaded.name if loaded else '设备配置未匹配' if s and s.get('valid') else '尚未加载波形')
            pending=local and (not loaded or fingerprint(loaded)!=fingerprint(local))
            detail=trigger_description(loaded) if loaded else '连接后按设备配置确认'
            if pending:detail+=f' · 待准备：{local.name}'
            row['detail'].setText(detail);row['name'].setToolTip(detail)
            row['status'].setText(f'{STATES.get(s["state"],"未知")}\n原码 {s["code"]}' if s else '未连接' if not self.connected else '等待回读')
            row['plot'].set_profiles(loaded,None,self.caps)
