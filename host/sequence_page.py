"""Sequence library/editor. Only explicit channel actions operate hardware."""
import copy
import json
import uuid
from dataclasses import replace
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QWidget,QVBoxLayout,QHBoxLayout,QGridLayout,QLabel,QLineEdit,QComboBox,QSpinBox,
    QListWidget,QListWidgetItem,QTableWidget,QTableWidgetItem,QHeaderView,QAbstractItemView,QFrame,QScrollArea,QFileDialog,QMenu,QMessageBox,QLayout)
from .appearance import SoftButton,FieldGrid,fit_combo
from .model import Profile,Capabilities,compile_profile,fingerprint
from .time_input import TimeInput,display_time
from .resource_view import ResourceUsageView
from .plot import WaveformPlot


class SequencePage(QWidget):
    def __init__(self,studio):
        super().__init__();self.studio=studio;self.loading=True;self.steps=[];self.current_row=-1;self.saved=''
        self.profile=Profile(name='新执行序列',mode='sequence',initial=512,safe=512)
        outer=QVBoxLayout(self);outer.setContentsMargins(0,12,0,0)
        scroll=QScrollArea();scroll.setWidgetResizable(True);outer.addWidget(scroll)
        content=QWidget();scroll.setWidget(content);layout=QVBoxLayout(content);layout.setSpacing(15);layout.setSizeConstraint(QLayout.SetMinimumSize)
        title=QLabel('执行序列');title.setObjectName('title');layout.addWidget(title)
        note=QLabel('先保存波形 → 编排时间或触发步骤 → 保存序列 → 在 DAC 页面选择并准备');note.setWordWrap(True);layout.addWidget(note)
        def card():
            f=QFrame();f.setObjectName('card');v=QVBoxLayout(f);v.setContentsMargins(20,18,20,18);v.setSpacing(12);layout.addWidget(f);return v
        box=card();row=QHBoxLayout();self.library=fit_combo(QComboBox());row.addWidget(QLabel('序列库'));row.addWidget(self.library,1)
        new=SoftButton('新建');new.clicked.connect(self.new);row.addWidget(new)
        more=SoftButton('管理序列');menu=QMenu(more);self.library_actions={}
        for text,fn in [('复制',self.duplicate),('导入',self.import_file),('导出',self.export_file),('归档',self.archive)]:
            action=menu.addAction(text);action.triggered.connect(fn);self.library_actions[text]=action
        more.setMenu(menu);row.addWidget(more)
        box.addLayout(row);self.library.currentIndexChanged.connect(self.choose)
        row=QHBoxLayout();self.name=QLineEdit();row.addWidget(self.name,1)
        self.save_state=QLabel();self.save_state.setObjectName('subtitle');row.addWidget(self.save_state)
        self.save_button=SoftButton('保存执行序列');self.save_button.setObjectName('primary');self.save_button.clicked.connect(self.save);row.addWidget(self.save_button);box.addLayout(row)
        self.mode=fit_combo(QComboBox());self.mode.addItem('固定时间表 · 一次触发后按时间播放','timeline');self.mode.addItem('事件序列 · 每步等待边沿 / 门控 / 延时','events')
        box.addWidget(QLabel('执行方式'));box.addWidget(self.mode);self.mode.currentIndexChanged.connect(self.edited)
        self.main_fields=FieldGrid(3,190);box.addWidget(self.main_fields)
        self.initial=QSpinBox();self.initial.setRange(0,16383);self.safe=QSpinBox();self.safe.setRange(0,16383)
        self.repeat=QSpinBox();self.repeat.setRange(1,65535)
        self.repeat.setSuffix(' 次')
        self.repeat_mode=QComboBox();self.repeat_mode.addItem('指定次数','finite');self.repeat_mode.addItem('无限重复 · ∞','infinite')
        self.repeat_box=QWidget();repeat_layout=QHBoxLayout(self.repeat_box);repeat_layout.setContentsMargins(0,0,0,0)
        repeat_layout.addWidget(self.repeat_mode,1);repeat_layout.addWidget(self.repeat,1)
        self.repeat_mode.currentIndexChanged.connect(self.edited)
        self.trigger=QComboBox();self.trigger.addItem('外部触发','external');self.trigger.addItem('软件触发','software')
        self.source=QComboBox();self.edge=QComboBox();self.edge.addItem('上升沿','rising');self.edge.addItem('下降沿','falling')
        self.source_label=QLabel('起始触发输入')
        for text,w in [('统一初始 / 等待码',self.initial),('统一中止码',self.safe),('整表重复',self.repeat),('起始触发源',self.trigger),(self.source_label,self.source),('起始边沿',self.edge)]:
            self.main_fields.add_field(text,self.repeat_box if w is self.repeat else w)
            (w.valueChanged if hasattr(w,'valueChanged') else w.currentIndexChanged).connect(self.edited)
        self.name.textChanged.connect(self.edited)
        self.hint=QLabel();self.hint.setWordWrap(True);box.addWidget(self.hint)
        box=card();row=QHBoxLayout();self.wave=fit_combo(QComboBox());row.addWidget(QLabel('保存的波形'));row.addWidget(self.wave,1)
        self.add_button=SoftButton('＋ 添加步骤');self.add_button.clicked.connect(self.add);row.addWidget(self.add_button)
        box.addLayout(row)
        self.table=QTableWidget(0,6);self.table.setHorizontalHeaderLabels(['步骤 / 波形快照','开始条件','开始 / 延时','触发输入','边沿 / 行为','次数'])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch);self.table.verticalHeader().hide();self.table.setMinimumHeight(220)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows);self.table.setSelectionMode(QAbstractItemView.SingleSelection);self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.itemSelectionChanged.connect(self.select_step);box.addWidget(self.table)
        row=QHBoxLayout();self.step_actions={}
        for text,fn in [('↑ 上移',lambda:self.move(-1)),('↓ 下移',lambda:self.move(1)),('删除步骤',self.remove)]:
            b=SoftButton(text);b.clicked.connect(fn);row.addWidget(b);self.step_actions[text]=b
        row.addStretch();box.addLayout(row)
        row=QHBoxLayout();self.update_button=SoftButton('更新所选波形快照');self.update_button.clicked.connect(self.update_snapshot);row.addWidget(self.update_button)
        self.common_button=SoftButton('全部使用批量输入');self.common_button.clicked.connect(self.common_input);row.addWidget(self.common_button);row.addStretch();box.addLayout(row)
        self.step_title=QLabel('选择步骤后编辑');self.step_title.setObjectName('section');box.addWidget(self.step_title)
        self.step_fields=FieldGrid(3,190);box.addWidget(self.step_fields)
        self.kind=QComboBox();self.kind.addItem('等待新边沿','edge');self.kind.addItem('上升进入 / 下降返回','gate');self.kind.addItem('下一步就绪后延时','delay')
        self.time=TimeInput(maximum=1_000_000_000);self.step_source=QComboBox();self.step_edge=QComboBox();self.step_edge.addItem('上升沿','rising');self.step_edge.addItem('下降沿','falling')
        self.step_repeat=QSpinBox();self.step_repeat.setRange(1,65535)
        self.time_label=QLabel('开始时间（相对序列起点）')
        for text,w in [('步骤开始条件',self.kind),(self.time_label,self.time),('步骤输入',self.step_source),('步骤边沿',self.step_edge),('步骤内重复',self.step_repeat)]:
            self.step_fields.add_field(text,w)
            (w.valueChanged if hasattr(w,'valueChanged') else w.currentIndexChanged).connect(self.step_edited)
        box=card();self.summary=QLabel();self.summary.setWordWrap(True);box.addWidget(self.summary)
        self.check=SoftButton('完整离散与容量检查');self.check.clicked.connect(self.check_full);box.addWidget(self.check)
        self.resource_view=ResourceUsageView();box.addWidget(self.resource_view)
        self.plot=WaveformPlot();box.addWidget(self.plot)
        self.feedback=QLabel('选择波形后添加步骤；波形快照随序列保存，不会随模板编辑自动变化。');self.feedback.setWordWrap(True);layout.addWidget(self.feedback)
        self.loading=False;self.refresh_library()
        draft=studio.library.root/'sequence_draft.json'
        previous=draft.read_text(encoding='utf-8') if draft.exists() else None
        self.new(force=True)
        if previous:
            try:self.load(Profile(**json.loads(previous)),saved=False)
            except (ValueError,TypeError,KeyError):pass

    def caps(self):return replace(self.studio.caps,sequence_steps=64) if not self.studio.connected else self.studio.caps

    def designer_caps(self):
        # Editing/saving is available before a firmware upgrade. Download always
        # validates against the connected device's advertised capabilities.
        return replace(self.caps(),sequence_steps=64,infinite_repeat=True,interpolation=True)

    def refresh_library(self):
        key=self.library.currentData();self.library.blockSignals(True);self.library.clear();self.library.addItem('新建 / 未保存',None)
        self.all_profiles=self.studio.library.list()
        for p in self.all_profiles:
            if p.mode=='sequence':self.library.addItem(p.name,p.id)
        self.library.setCurrentIndex(max(0,self.library.findData(key)));self.library.blockSignals(False)
        key=self.wave.currentData();self.wave.clear()
        for p in self.all_profiles:
            if p.mode in ('ramp','function'):self.wave.addItem(f'{p.name} · {p.dac_bits} bit · 初始 {p.initial} · '+(f'插值 ≤ {p.interpolation_error} 原码' if p.playback=='linear' else '逐码保持'),p.id)
        self.wave.setCurrentIndex(max(0,self.wave.findData(key)))
        self.add_button.setEnabled(self.wave.count()>0)

    def refresh_context(self):
        self.loading=True
        count=self.studio.caps.trigger_count
        for combo in (self.source,self.step_source):
            key=combo.currentData() or 0;combo.clear()
            for i in range(count):combo.addItem(f'输入 {i+1}',i)
            if key>=count:combo.addItem(f'输入 {key+1} · 当前不可用',key)
            combo.setCurrentIndex(combo.findData(key))
        self.loading=False;self.edited()

    def collect(self):
        return replace(self.profile,name=self.name.text(),mode='sequence',sequence_mode=self.mode.currentData(),sequence_steps=copy.deepcopy(self.steps),
                       initial=self.initial.value(),safe=self.safe.value(),repeat=0 if self.repeat_mode.currentData()=='infinite' and self.mode.currentData()=='timeline' else self.repeat.value(),trigger=self.trigger.currentData(),
                       trigger_input=self.source.currentData() or 0,trigger_edge=self.edge.currentData())

    def maybe_discard(self):
        current=fingerprint(self.collect())
        if current==self.saved or not self.saved and not self.steps and current==self.loaded_fingerprint:return True
        answer=QMessageBox.question(self,'未保存的序列','当前序列有未保存修改。保存后继续？',QMessageBox.Save|QMessageBox.Discard|QMessageBox.Cancel,QMessageBox.Save)
        if answer==QMessageBox.Cancel:return False
        return self.save() if answer==QMessageBox.Save else True

    def open_profile(self,p):
        if p.id==self.profile.id:return True
        if not self.maybe_discard():return False
        self.load(p);return True

    def new(self,checked=False,*,force=False):
        if not force and not self.maybe_discard():return
        bits=self.studio.caps.dac_bits
        self.load(Profile(name='新执行序列',mode='sequence',dac_bits=bits,initial=1<<(bits-1),safe=1<<(bits-1),sequence_steps=[]),saved=False)

    def load(self,p,saved=True):
        self.loading=True;self.profile=copy.deepcopy(p);self.steps=copy.deepcopy(p.sequence_steps);self.current_row=-1
        self.name.setText(p.name);self.mode.setCurrentIndex(self.mode.findData(p.sequence_mode));self.initial.setValue(p.initial);self.safe.setValue(p.safe);self.repeat.setValue(p.repeat or 1)
        self.repeat_mode.setCurrentIndex(self.repeat_mode.findData('infinite' if p.repeat==0 else 'finite'))
        self.trigger.setCurrentIndex(self.trigger.findData(p.trigger));self.edge.setCurrentIndex(self.edge.findData(p.trigger_edge))
        for combo,value in ((self.source,p.trigger_input),(self.step_source,0)):
            combo.clear()
            for i in range(max(self.studio.caps.trigger_count,value+1)):combo.addItem(f'输入 {i+1}',i)
            combo.setCurrentIndex(combo.findData(value))
        self.library.blockSignals(True);self.library.setCurrentIndex(max(0,self.library.findData(p.id)));self.library.blockSignals(False)
        self.loaded_fingerprint=fingerprint(p);self.saved=fingerprint(p) if saved else '';self.loading=False;self.render_rows();self.edited()

    def choose(self):
        if self.loading:return
        p=next((p for p in self.all_profiles if p.id==self.library.currentData()),None)
        if p and self.open_profile(p):return
        self.library.blockSignals(True);self.library.setCurrentIndex(max(0,self.library.findData(self.profile.id)));self.library.blockSignals(False)

    def add(self):
        p=next((p for p in self.all_profiles if p.id==self.wave.currentData()),None)
        if not p:return
        start=0
        if self.steps:
            from .sequences import waveform
            last=self.steps[-1]
            try:start=last.get('start_ms',0)+compile_profile(waveform(last,self.collect()),replace(self.caps(),sequence_steps=64)).duration_ms*last.get('repeat',1)
            except ValueError:pass
        self.steps.append(dict(waveform=p.to_dict(),kind='gate' if p.timing_mode=='gate' else 'edge',start_ms=start,delay_ms=0,input=self.source.currentData() or 0,edge='rising',repeat=1))
        self.render_rows(len(self.steps)-1);self.edited()

    def render_rows(self,select=None):
        self.table.blockSignals(True);self.table.setRowCount(len(self.steps));events=self.mode.currentData()=='events'
        self.table.setHorizontalHeaderLabels(['步骤 / 波形快照','开始条件','就绪后延时' if events else '开始时间','触发输入','边沿 / 行为','次数'])
        for column in (1,3,4):self.table.setColumnHidden(column,not events)
        for i,s in enumerate(self.steps):
            value=s.get('delay_ms',0) if events else s.get('start_ms',0);v,u=display_time(value)
            edge=s.get('edge','rising') if events else self.edge.currentData()
            behavior='上升 / 下降' if events and s['kind']=='gate' else '—' if events and s['kind']=='delay' else '上升沿' if edge=='rising' else '下降沿'
            if not events and self.trigger.currentData()=='software':behavior='软件触发'
            vals=[f'{i+1}  {s["waveform"]["name"]}',{'edge':'等待边沿','gate':'电平门控','delay':'就绪后延时'}[s['kind']] if events else '按固定时间',f'{v:g} {u}' if not events or s['kind']=='delay' else '—',f'输入 {s.get("input",0)+1}' if events and s['kind']!='delay' else '统一起始' if not events else '—',behavior,str(s.get('repeat',1))]
            for j,v in enumerate(vals):
                item=QTableWidgetItem(v);item.setToolTip(v);self.table.setItem(i,j,item)
            self.table.setRowHeight(i,48)
        self.table.blockSignals(False)
        index=self.current_row if select is None else select
        if self.steps:self.table.selectRow(max(0,min(index,len(self.steps)-1)));self.select_step()
        else:self.current_row=-1;self.update_step_controls()

    def select_step(self):
        if self.loading:return
        i=self.table.currentRow()
        if not 0<=i<len(self.steps):return
        self.current_row=i;s=self.steps[i];self.loading=True
        self.kind.setCurrentIndex(self.kind.findData(s['kind']));self.time.setValue(s.get('delay_ms',0) if self.mode.currentData()=='events' else s.get('start_ms',0))
        value=s.get('input',0)
        if self.step_source.findData(value)<0:self.step_source.addItem(f'输入 {value+1} · 当前不可用',value)
        self.step_source.setCurrentIndex(self.step_source.findData(value));self.step_edge.setCurrentIndex(self.step_edge.findData(s.get('edge','rising')));self.step_repeat.setValue(s.get('repeat',1));self.loading=False;self.update_step_controls()

    def update_step_controls(self):
        events=self.mode.currentData()=='events';selected=0<=self.current_row<len(self.steps);kind=self.kind.currentData()
        self.step_title.setText(f'步骤 {self.current_row+1} · 编辑所选步骤' if selected else '尚无步骤 · 请先添加保存的波形')
        self.step_fields.setVisible(selected)
        self.kind.setEnabled(selected and events);self.step_source.setEnabled(selected and events and kind!='delay')
        self.step_edge.setEnabled(selected and events and kind=='edge');self.step_repeat.setEnabled(selected and (not events or kind!='gate'))
        self.time.setEnabled(selected and (not events or kind=='delay'))
        for control,visible in ((self.kind,events),(self.time,not events or kind=='delay'),(self.step_source,events and kind!='delay'),(self.step_edge,events and kind=='edge'),(self.step_repeat,not events or kind!='gate')):
            self.step_fields.show_field(control,visible)
        self.time_label.setText('步骤就绪后等待' if events else '开始时间（相对序列起点）')
        self.step_actions['↑ 上移'].setEnabled(selected and self.current_row>0)
        self.step_actions['↓ 下移'].setEnabled(selected and self.current_row<len(self.steps)-1)
        self.step_actions['删除步骤'].setEnabled(selected);self.update_button.setEnabled(selected)
        self.common_button.setVisible(events);self.common_button.setEnabled(events and bool(self.steps))

    def step_edited(self):
        if self.loading or not 0<=self.current_row<len(self.steps):return
        s=self.steps[self.current_row];events=self.mode.currentData()=='events'
        s.update(kind=self.kind.currentData(),input=self.step_source.currentData() or 0,edge=self.step_edge.currentData(),repeat=self.step_repeat.value())
        if events and s['kind']=='gate':s.update(edge='rising',repeat=1)
        s['delay_ms' if events else 'start_ms']=self.time.value()
        self.render_rows();self.edited()

    def edited(self):
        if self.loading:return
        events=self.mode.currentData()=='events';self.loading=True
        if events:self.trigger.setCurrentIndex(0);self.repeat.setValue(1);self.repeat_mode.setCurrentIndex(0)
        infinite=not events and self.repeat_mode.currentData()=='infinite'
        self.repeat_mode.setEnabled(not events);self.repeat.setVisible(not infinite);self.repeat.setEnabled(not events)
        self.repeat_mode.setToolTip('事件序列完成后自动回到第一步等待新事件。' if events else '选择无限重复后，整表循环播放，直到点击中止。')
        external=self.trigger.currentData()=='external'
        self.trigger.setEnabled(not events);self.edge.setEnabled(not events and external);self.source.setEnabled(events or external)
        self.source_label.setText('新增 / 批量触发输入' if events else '起始触发输入')
        for control,visible in ((self.repeat_box,not events),(self.trigger,not events),(self.source,events or external),(self.edge,not events and external)):
            self.main_fields.show_field(control,visible)
        self.hint.setText('各步可使用相同或不同输入；第一步等待边沿/门控。完成整表后从第一步重新等待。忙时不排队，门控的升降沿来自同一输入。' if events else '一次起始触发后按表内开始时间播放；步骤时间不能重叠，间隔保持统一初始码。不同 DAC 可设置不同起始输入。')
        self.hint.setText(self.hint.text()+(' 各步骤保留波形自己的播放编码与误差设置，可混合使用。' if events else ' 固定时间表的波形须使用同一种播放编码；不会自动转换已有快照。'))
        if infinite:
            self.hint.setText('一次起始触发后整表无限循环；每轮重用表内相对时间，轮间不额外等待触发。点击“中止”停止并输出中止码。预览仅显示一轮。')
            if self.studio.connected and not self.studio.caps.infinite_repeat:
                self.hint.setText(self.hint.text()+' 当前设备需要升级固件后才能下载无限重复方案。')
        self.loading=False;self.render_rows();self.update_step_controls();p=self.collect();self.plot.caps=self.designer_caps();self.valid=False
        try:
            c=compile_profile(p,self.designer_caps());self.plot.set_profile(p);self.valid=True
            v,u=display_time(c.duration_ms)
            playback='执行时间由事件决定 · 每轮重新等待' if events else f'单轮 {v:g} {u} · '+('∞ 无限重复，直到中止' if infinite else f'整表重复 {p.repeat} 次')
            self.summary.setText(f'{len(self.steps)} 个步骤 · {p.dac_bits} bit · '+playback+' · '+('已保存' if fingerprint(p)==self.saved else '未保存修改'))
        except ValueError as exc:self.summary.setText(str(exc));self.plot.set_profile(None)
        self.save_state.setText('参数待修正' if not self.valid else '已保存' if fingerprint(p)==self.saved else '未保存修改')
        self.save_button.setEnabled(self.valid and not self.studio.busy)
        self.library_actions['导出'].setEnabled(self.valid)
        self.library_actions['归档'].setEnabled(any(x.id==self.profile.id for x in self.all_profiles))
        try:
            path=self.studio.library.root/'sequence_draft.json';temp=path.with_suffix('.tmp');temp.write_text(json.dumps(p.to_dict(),ensure_ascii=False,allow_nan=False),encoding='utf-8');temp.replace(path)
        except (OSError,ValueError):pass
        if hasattr(self,'check'):self.check.setEnabled(not self.studio.busy)
        if hasattr(self.studio,'board_page'):self.studio.refresh_resources()

    def save(self):
        try:
            p=self.collect();compile_profile(p,self.designer_caps());self.studio.library.save(p);self.profile=p;self.saved=fingerprint(p);self.studio.refresh_library();self.library.blockSignals(True);self.library.setCurrentIndex(self.library.findData(p.id));self.library.blockSignals(False);self.edited();self.feedback.setText('执行序列及波形快照已保存。请进入 DAC 页面选择并下载。');return True
        except ValueError as exc:self.feedback.setText(str(exc));return False

    def check_full(self):
        try:
            p=self.collect();compile_profile(p,self.designer_caps());self.check_fingerprint=fingerprint(p);self.studio.send('compile_sequence',dict(profile=p,caps=self.studio.resource_caps()))
        except ValueError as exc:self.feedback.setText(str(exc))

    def result(self,result):
        if self.check_fingerprint!=fingerprint(self.collect()):
            self.feedback.setText("检查期间序列已改变，此结果对应旧版本，请重新检查。");return
        if len(result.runs)>self.caps().max_runs:
            self.feedback.setText(f'容量不足：完整离散为 {len(result.runs)} 段，当前板卡每路容量为 {self.caps().max_runs} 段。请减少波形变化或步骤。');return
        if getattr(result,'interpolation',False):
            self.feedback.setText(f'完整检查通过：插值/保持段池 {len(result.runs)} / {self.caps().max_runs}；{len(result.steps) or len(self.steps)} 步；全部 {result.checked_samples:,} 点，最大差异 {result.max_error:g} / 允许 {result.error_limit} 原码')
        else:self.feedback.setText(f'完整离散通过：波形池 {len(result.runs)} 段 / 当前每路容量 {self.caps().max_runs}；{len(result.steps) or len(self.steps)} 步；最大取整误差 {result.max_error:.4g} LSB')

    def duplicate(self):
        p=self.collect();p.id=uuid.uuid4().hex;p.name+=' · 副本';self.load(p,saved=False)

    def move(self,delta):
        i=self.current_row;j=i+delta
        if 0<=i<len(self.steps) and 0<=j<len(self.steps):self.steps[i],self.steps[j]=self.steps[j],self.steps[i];self.render_rows(j);self.edited()

    def remove(self):
        if 0<=self.current_row<len(self.steps):self.steps.pop(self.current_row);self.render_rows();self.edited()

    def common_input(self):
        if self.mode.currentData()!='events':return
        for s in self.steps:s['input']=self.source.currentData() or 0
        self.render_rows();self.edited()

    def update_snapshot(self):
        if not 0<=self.current_row<len(self.steps):return
        s=self.steps[self.current_row];p=next((p for p in self.all_profiles if p.id==s['waveform']['id']),None)
        if p:s['waveform']=p.to_dict();self.render_rows();self.edited()
        else:self.feedback.setText('原波形已不存在，保存快照仍可使用；需要替换时重新添加步骤。')

    def archive(self):
        if not self.maybe_discard():return
        self.studio.library.archive(self.profile);self.studio.refresh_library();self.new(force=True)

    def import_file(self):
        path,_=QFileDialog.getOpenFileName(self,'导入执行序列','','JSON (*.json)')
        if not path:return
        try:
            from pathlib import Path
            p=Profile.from_dict(json.loads(Path(path).read_text(encoding='utf-8')))
            if p.mode!='sequence':raise ValueError('请选择执行序列 JSON')
            if not self.maybe_discard():return
            p.id=uuid.uuid4().hex;self.load(p,saved=False)
        except (ValueError,TypeError,KeyError,OSError) as exc:self.feedback.setText(str(exc))

    def export_file(self):
        path,_=QFileDialog.getSaveFileName(self,'导出含波形快照的序列','','JSON (*.json)')
        if path:
            try:
                from pathlib import Path
                p=self.collect();compile_profile(p,self.designer_caps());Path(path).write_text(json.dumps(p.to_dict(),ensure_ascii=False,indent=2),encoding='utf-8')
            except (ValueError,OSError) as exc:self.feedback.setText(str(exc))
