"""A dedicated designer for event-driven scans, separate from timed waveforms."""
import copy,json,uuid
from dataclasses import replace
from PySide6.QtWidgets import QWidget,QVBoxLayout,QHBoxLayout,QLabel,QFrame,QScrollArea,QLineEdit,QComboBox,QSpinBox,QMenu,QMessageBox,QFileDialog
from .appearance import SoftButton,FieldGrid,fit_combo
from .model import Profile,Segment,Capabilities,compile_profile,fingerprint
from .output_plot import OutputWaveformPlot
from .resource_view import ResourceUsageView


class ScanPage(QWidget):
    def __init__(self,studio):
        super().__init__();self.studio=studio;self.loading=True;self.saved='';self.valid=False
        self.path=studio.library.root/'scan_draft.json'
        previous=self.path.read_text(encoding='utf-8') if self.path.exists() else None
        outer=QVBoxLayout(self);outer.setContentsMargins(0,12,0,0)
        scroll=QScrollArea();scroll.setWidgetResizable(True);outer.addWidget(scroll)
        content=QWidget();scroll.setWidget(content);layout=QVBoxLayout(content);layout.setSpacing(14)
        title=QLabel('触发步进扫描');title.setObjectName('title');layout.addWidget(title)
        note=QLabel('每次触发输出一个扫描点，两次触发之间保持当前原码。保存后可分配给任意 DAC。');note.setWordWrap(True);layout.addWidget(note)
        def card():
            frame=QFrame();frame.setObjectName('card');box=QVBoxLayout(frame);box.setContentsMargins(20,18,20,18);box.setSpacing(12);layout.addWidget(frame);return box
        box=card();row=QHBoxLayout();row.addWidget(QLabel('扫描方案'));self.library=fit_combo(QComboBox());row.addWidget(self.library,1)
        self.new_button=SoftButton('新建');self.new_button.clicked.connect(self.new);row.addWidget(self.new_button)
        more=SoftButton('管理方案');menu=QMenu(more)
        for title,fn in [('复制',self.duplicate),('导入',self.import_file),('导出',self.export_file),('归档',self.archive)]:menu.addAction(title).triggered.connect(fn)
        more.setMenu(menu);row.addWidget(more);box.addLayout(row)
        row=QHBoxLayout();self.name=QLineEdit();row.addWidget(self.name,1);self.save_state=QLabel();row.addWidget(self.save_state)
        self.save_button=SoftButton('保存扫描方案');self.save_button.setObjectName('primary');self.save_button.clicked.connect(self.save);row.addWidget(self.save_button);box.addLayout(row)
        self.fields=FieldGrid(3,185);box.addWidget(self.fields)
        self.start=QSpinBox();self.start.setRange(0,16383)
        self.step=QSpinBox();self.step.setRange(-16383,16383);self.step.setToolTip('正数递增，负数递减，0 保持相同原码')
        self.points=QSpinBox();self.points.setRange(1,65535)
        self.initial=QSpinBox();self.initial.setRange(0,16383)
        self.safe=QSpinBox();self.safe.setRange(0,16383)
        self.bits=QComboBox()
        for bits in (10,12,14):self.bits.addItem(f'{bits} bit',bits)
        for title,field in [('扫描起始码',self.start),('每次步长（原码）',self.step),('扫描点数',self.points),('首次触发前 / 取消布防码',self.initial),('中止码',self.safe),('方案 DAC 位数',self.bits)]:self.fields.add_field(title,field)
        self.end=QComboBox();self.end.addItem('末点保持并结束 · 重新布防后从头开始','hold');self.end.addItem('循环扫描 · 下一次触发回到起始点','loop')
        box.addWidget(QLabel('到达末点后'));box.addWidget(self.end)
        self.trigger_fields=FieldGrid(3,185);box.addWidget(self.trigger_fields)
        self.trigger=QComboBox();self.trigger.addItem('外部触发','external');self.trigger.addItem('软件单步','software')
        self.source=QComboBox();self.edge=QComboBox();self.edge.addItem('上升沿','rising');self.edge.addItem('下降沿','falling')
        for title,field in [('触发方式',self.trigger),('触发输入',self.source),('有效边沿',self.edge)]:self.trigger_fields.add_field(title,field)
        self.summary=QLabel();self.summary.setWordWrap(True);box.addWidget(self.summary)
        self.feedback=QLabel();self.feedback.setWordWrap(True);self.feedback.setObjectName('subtitle');box.addWidget(self.feedback)
        self.resource_view=ResourceUsageView();box.addWidget(self.resource_view)
        box=card();title=QLabel('扫描点预览');title.setObjectName('section');box.addWidget(title)
        self.plot=OutputWaveformPlot();box.addWidget(self.plot)
        note=QLabel('横轴为触发次数，不是时间。0 为等待码，第 1 次触发输出起始码；后续每次触发推进一步。大量点时预览抽样显示。');note.setObjectName('subtitle');note.setWordWrap(True);box.addWidget(note)
        for field in (self.start,self.step,self.points,self.initial,self.safe,self.bits,self.end,self.trigger,self.source,self.edge):
            (field.valueChanged if hasattr(field,'valueChanged') else field.currentIndexChanged).connect(self.edited)
        self.name.textChanged.connect(self.edited);self.library.currentIndexChanged.connect(self.choose)
        self.profile=Profile(name='新触发步进扫描',mode='scan',initial=512,safe=512)
        self.refresh_library();self.load(self.profile,saved=False,fresh=True)
        if previous:
            try:
                data=json.loads(previous);values=data['profile'];values['segments']=[Segment(**s) for s in values['segments']]
                p=Profile(**values)
                if p.mode=='scan':self.load(p,saved=False,saved_fingerprint=data.get('saved',''),fresh=bool(data.get('fresh',False)))
            except (ValueError,TypeError,KeyError):studio.notice('扫描草稿读取失败，请从保存的方案恢复')

    def caps(self):return replace(self.studio.caps,dac_bits=self.bits.currentData(),trigger_scan=True)

    def collect(self):
        return replace(self.profile,mode='scan',name=self.name.text(),channel=0,dac_bits=self.bits.currentData(),initial=self.initial.value(),safe=self.safe.value(),
            scan_start=self.start.value(),scan_step=self.step.value(),scan_points=self.points.value(),scan_end=self.end.currentData(),
            trigger=self.trigger.currentData(),trigger_input=self.source.currentData() or 0,trigger_edge=self.edge.currentData(),repeat=1,timing_mode='fixed')

    def refresh_library(self):
        self.profiles=[p for p in self.studio.library.list() if p.mode=='scan']
        self.library.blockSignals(True);self.library.clear();self.library.addItem('新建 / 未保存',None)
        for p in self.profiles:self.library.addItem(f'{p.name} · {p.dac_bits} bit',p.id)
        self.library.setCurrentIndex(max(0,self.library.findData(self.profile.id)));self.library.blockSignals(False)

    def refresh_context(self):
        self.loading=True;key=self.source.currentData() or 0;self.source.clear()
        for index in range(max(self.studio.caps.trigger_count,key+1)):
            self.source.addItem(f'输入 {index+1}'+(' · 当前不可用' if index>=self.studio.caps.trigger_count else ''),index)
        self.source.setCurrentIndex(self.source.findData(key));self.loading=False;self.edited()

    def load(self,p,saved=True,*,saved_fingerprint=None,fresh=False):
        self.loading=True;self.profile=copy.deepcopy(p);self.name.setText(p.name)
        for field,value in ((self.start,p.scan_start),(self.step,p.scan_step),(self.points,p.scan_points),(self.initial,p.initial),(self.safe,p.safe)):field.setValue(value)
        for field,value in ((self.bits,p.dac_bits),(self.end,p.scan_end),(self.trigger,p.trigger),(self.edge,p.trigger_edge)):field.setCurrentIndex(field.findData(value))
        self.source.clear()
        for i in range(max(self.studio.caps.trigger_count,p.trigger_input+1)):self.source.addItem(f'输入 {i+1}',i)
        self.source.setCurrentIndex(self.source.findData(p.trigger_input))
        self.saved=fingerprint(p) if saved else (saved_fingerprint or '');self.loaded=fingerprint(p);self.fresh=fresh
        self.loading=False;self.refresh_library();self.edited()

    def edited(self):
        if self.loading:return
        p=self.collect();external=p.trigger=='external'
        self.trigger_fields.show_field(self.source,external);self.trigger_fields.show_field(self.edge,external)
        try:
            compile_profile(p,self.caps());self.valid=True;last=p.scan_start+(p.scan_points-1)*p.scan_step
            example=[p.scan_start+i*p.scan_step for i in range(min(4,p.scan_points))]
            self.summary.setText('触发输出：'+' → '.join(map(str,example))+(' → …' if p.scan_points>4 else '')+f' · 末点 {last} · 共 {p.scan_points:,} 点')
            self.feedback.setText('第 1 次触发输出起始码；当前方案可共用触发输入，也可在各 DAC 页单独设置输入。'+('连接的固件需要升级后才能下载扫描方案。' if self.studio.connected and not self.studio.caps.trigger_scan else ''))
            self.feedback.setStyleSheet('');self.plot.set_profiles(p,None,self.caps())
        except ValueError as exc:
            self.valid=False;self.summary.setText('参数需要修正');self.feedback.setText(str(exc));self.feedback.setStyleSheet('color:#b3594b;');self.plot.set_profiles(None,None,self.caps(),empty=str(exc))
        self.save_state.setText('已保存' if fingerprint(p)==self.saved else '未保存修改' if self.valid else '参数待修正')
        self.save_button.setEnabled(self.valid and not self.studio.busy)
        self.stash()
        if hasattr(self.studio,'board_page'):self.studio.refresh_resources()

    def stash(self):
        try:
            p=self.collect();temp=self.path.with_suffix('.tmp');temp.write_text(json.dumps({'profile':p.to_dict(),'saved':self.saved,'fresh':self.fresh and fingerprint(p)==self.loaded},ensure_ascii=False,indent=2),encoding='utf-8');temp.replace(self.path)
        except OSError as exc:self.studio.notice(f'扫描草稿未写入磁盘：{exc}')

    def maybe_discard(self):
        current=fingerprint(self.collect())
        if current==self.saved or not self.saved and self.fresh and current==self.loaded:return True
        answer=QMessageBox.question(self,'未保存的扫描','当前扫描方案有未保存修改。保存后继续？',QMessageBox.Save|QMessageBox.Discard|QMessageBox.Cancel,QMessageBox.Save)
        if answer==QMessageBox.Cancel:return False
        return self.save() if answer==QMessageBox.Save else True

    def open_profile(self,p):
        if p.id==self.profile.id:return True
        if not self.maybe_discard():return False
        self.load(p);return True

    def choose(self):
        p=next((p for p in self.profiles if p.id==self.library.currentData()),None)
        if p and self.open_profile(p):return
        self.refresh_library()

    def new(self,checked=False,*,force=False):
        if force or self.maybe_discard():
            bits=self.studio.caps.dac_bits;mid=1<<(bits-1)
            self.load(Profile(mode='scan',name='新触发步进扫描',initial=mid,safe=mid,dac_bits=bits),saved=False,fresh=True)

    def save(self):
        try:
            p=self.collect();compile_profile(p,self.caps());self.studio.library.save(p);self.profile=p;self.saved=fingerprint(p)
            self.studio.refresh_library();self.edited();self.studio.notice('扫描方案已保存；在 DAC 页面选择并准备后生效');return True
        except (ValueError,OSError) as exc:self.feedback.setText(str(exc));return False

    def duplicate(self):
        p=replace(self.collect(),id=uuid.uuid4().hex,name=self.name.text()[:85]+' · 副本');self.load(p,saved=False)

    def import_file(self):
        path,_=QFileDialog.getOpenFileName(self,'导入扫描方案','','JSON (*.json)')
        if not path:return
        try:
            from pathlib import Path
            p=Profile.from_dict(json.loads(Path(path).read_text(encoding='utf-8')))
            if p.mode!='scan':raise ValueError('请导入触发步进扫描方案；其他方案使用对应设计页')
            if self.maybe_discard():self.load(replace(p,id=uuid.uuid4().hex),saved=False)
        except (ValueError,KeyError,TypeError,OSError) as exc:self.feedback.setText(str(exc))

    def export_file(self):
        try:
            p=self.collect();compile_profile(p,self.caps())
            path,_=QFileDialog.getSaveFileName(self,'导出扫描方案',p.name+'.json','JSON (*.json)')
            if path:
                from pathlib import Path
                Path(path).write_text(json.dumps(p.to_dict(),ensure_ascii=False,indent=2),encoding='utf-8')
        except (ValueError,OSError) as exc:self.feedback.setText(str(exc))

    def archive(self):
        if self.maybe_discard():
            self.studio.library.archive(self.profile);self.studio.refresh_library();self.new(force=True)
