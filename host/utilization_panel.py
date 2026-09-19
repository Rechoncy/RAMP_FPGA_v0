"""Physical FPGA usage and its persistent sidebar summary share one report."""
import json
from pathlib import Path
from PySide6.QtCore import Qt,Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtCore import QUrl
from PySide6.QtWidgets import QWidget,QFrame,QVBoxLayout,QHBoxLayout,QGridLayout,QLabel,QProgressBar,QFileDialog,QMenu,QSizePolicy
from .appearance import SoftButton,FieldGrid,Foldout
from .board import Board
from .runtime import resource_root
from .utilization import read_report,read_build_manifest,find_latest_build,board_fingerprint,source_fingerprint


def number(value):
    return '—' if value is None else f'{value:,.0f}' if value==int(value) else f'{value:,.1f}'


class UtilizationPanel(QWidget):
    build_requested=Signal()
    changed=Signal()

    def __init__(self,data_root):
        super().__init__();self.root=Path(data_root);self.selection_path=self.root/'resource_report_selection.json'
        self.board=None;self.record=None;self.report=None;self.manifest=None;self.error='';self.context_error=''
        self.reference=resource_root()/'host/assets/reference-firmware/trigger-scan'
        self.current_sources=source_fingerprint(resource_root())
        box=QVBoxLayout(self);box.setContentsMargins(0,0,0,0);box.setSpacing(10);box.setAlignment(Qt.AlignTop)
        row=QHBoxLayout();title=QLabel('FPGA 硬件资源');title.setObjectName('section');row.addWidget(title);row.addStretch()
        self.stage=QLabel();self.stage.setObjectName('subtitle');row.addWidget(self.stage);box.addLayout(row)
        self.origin=QLabel();self.origin.setWordWrap(True);self.origin.setObjectName('subtitle');box.addWidget(self.origin)
        self.grid=FieldGrid(4,130);box.addWidget(self.grid);self.metrics={}
        for key,title in [('lut','LUT'),('ff','寄存器 FF'),('bram','BRAM · 36 Kb 等效'),('dsp','DSP')]:
            cell=QFrame();cell.setObjectName('physicalMetric');cell.setStyleSheet('QFrame#physicalMetric{background:#f3f7ff;border:1px solid #e2ebfa;border-radius:12px;}')
            v=QVBoxLayout(cell);v.setContentsMargins(12,10,12,10);v.setSpacing(6)
            percent=QLabel('—');percent.setStyleSheet('font-size:19px;font-weight:600;color:#2563eb;');v.addWidget(percent)
            count=QLabel('已用 / 总量');count.setStyleSheet('font-size:11px;color:#647c9e;');v.addWidget(count)
            bar=QProgressBar();bar.setRange(0,1000);bar.setFixedHeight(5);bar.setTextVisible(False);v.addWidget(bar)
            self.grid.add_field(title,cell);self.metrics[key]=dict(percent=percent,count=count,bar=bar)
        self.state=QLabel();self.state.setWordWrap(True);self.state.setSizePolicy(QSizePolicy.Ignored,QSizePolicy.Preferred);box.addWidget(self.state)
        actions=QHBoxLayout();self.refresh_button=SoftButton('刷新构建报告');self.refresh_button.clicked.connect(self.refresh_latest);actions.addWidget(self.refresh_button)
        self.import_button=SoftButton('导入报告');self.import_button.clicked.connect(self.choose_report);actions.addWidget(self.import_button)
        more=SoftButton('更多');menu=QMenu(more)
        menu.addAction('前往生成并综合').triggered.connect(self.build_requested.emit)
        menu.addAction('查看发布固件参考').triggered.connect(self.select_reference)
        self.open_action=menu.addAction('打开原始报告');self.open_action.triggered.connect(self.open_report)
        more.setMenu(menu);actions.addWidget(more);actions.addStretch();box.addLayout(actions)
        self.details=Foldout('报告来源与关联');box.addWidget(self.details)
        self.details_label=QLabel();self.details_label.setWordWrap(True);self.details_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.details_label.setSizePolicy(QSizePolicy.Ignored,QSizePolicy.Preferred);self.details.body.addWidget(self.details_label)
        self.restore()

    def local_path(self,relative):
        path=(self.root/relative).resolve()
        if not path.is_relative_to(self.root.resolve()):raise ValueError('报告索引必须位于当前 user_data 内')
        return path

    def restore(self):
        try:
            if self.selection_path.exists():self.load(json.loads(self.selection_path.read_text(encoding='utf-8')))
            else:
                latest=find_latest_build(self.root/'builds')
                if latest:self.load(dict(kind='build',manifest=Path(latest['manifest_path']).relative_to(self.root).as_posix()))
                else:self.load(dict(kind='reference'))
        except (ValueError,OSError,KeyError,TypeError) as exc:
            self.error=f'报告无法读取：{exc}';self.render()

    def load(self,record,persist=False):
        kind=record['kind'];manifest=None
        if kind=='build':manifest=read_build_manifest(self.local_path(record['manifest']));report=manifest.get('report')
        elif kind=='import':
            report=read_report(self.local_path(record['report']))
            expected=record.get('sha256',Path(record['report']).parent.name)
            if report['sha256']!=expected:raise ValueError('导入报告副本内容已变化，请重新导入')
        elif kind=='reference':
            report=read_report(self.reference/'utilization.rpt')
            metadata=json.loads((self.reference/'metadata.json').read_text(encoding='utf-8'))
            if report['sha256']!=metadata['report_sha256']:raise ValueError('发布参考报告校验失败')
        else:raise ValueError('不支持的报告来源')
        if persist:
            temp=self.selection_path.with_suffix('.tmp');temp.write_text(json.dumps(record,ensure_ascii=False,indent=2),encoding='utf-8');temp.replace(self.selection_path)
        self.record=dict(record);self.report=report;self.manifest=manifest;self.error='';self.render()

    def refresh_latest(self):
        try:
            latest=find_latest_build(self.root/'builds')
            if not latest:
                self.error='暂无本机构建报告；选择“更多 → 前往生成并综合”，完成后自动更新。';self.render();return
            if latest.get('mode')=='generate' and latest.get('status')=='success' and not latest.get('report') and self.report:
                self.error='最新构建仅生成工程，没有新的资源统计；仍显示上方原报告。';self.render();return
            self.load(dict(kind='build',manifest=Path(latest['manifest_path']).relative_to(self.root).as_posix()),persist=True)
        except (ValueError,OSError,KeyError,TypeError) as exc:self.error=f'构建报告未更新：{exc}';self.render()

    def select_reference(self):
        try:self.load(dict(kind='reference'),persist=True)
        except (ValueError,OSError,KeyError) as exc:self.error=f'参考报告无法读取：{exc}';self.render()

    def choose_report(self):
        path,_=QFileDialog.getOpenFileName(self,'导入 Vivado 资源占用报告','','Vivado utilization (*.rpt);;所有文件 (*)')
        if path:self.import_report(path)

    def import_report(self,path):
        try:
            parsed=read_report(path);folder=self.root/'resource_reports'/parsed['sha256'];folder.mkdir(parents=True,exist_ok=True)
            target=folder/'utilization.rpt';payload=Path(path).read_bytes()
            import hashlib
            if hashlib.sha256(payload).hexdigest()!=parsed['sha256']:raise ValueError('读取期间报告内容变化，请重新导入')
            target.write_bytes(payload)
            self.load(dict(kind='import',report=target.relative_to(self.root).as_posix(),sha256=parsed['sha256'],original_name=Path(path).name),persist=True);return True
        except (ValueError,OSError,KeyError,TypeError) as exc:self.error=f'导入未完成：{exc}';self.render();return False

    def set_board(self,board,error=''):
        self.board=board;self.context_error=error;self.render()

    def open_report(self):
        if self.report:QDesktopServices.openUrl(QUrl.fromLocalFile(self.report['path']))

    def render(self):
        report=self.report;kind=(self.record or {}).get('kind');messages=[]
        # A bundled reference belongs to one part. Never fill another FPGA's
        # resource totals from that unrelated reference by default.
        part_matches=bool(report and self.board and self.board.part.lower().strip()==report.get('device','').lower().strip())
        visible=report if kind!='reference' or part_matches else None
        self.stage.setText({'routed':'布局布线','synth':'综合','unknown':'阶段未识别'}.get(visible.get('stage'),'阶段未识别') if visible else '暂无报告')
        source={'build':'本机构建','import':'导入报告','reference':'发布固件参考 · 09133301'}.get(kind,'资源报告')
        self.origin.setText(f'{source} · {report.get("device") or "器件未识别"} · {report.get("date") or "时间未提供"}' if visible else '生成并综合后自动显示，也可导入已有 Vivado utilization.rpt。')
        caution=False
        if self.context_error:messages.append('板卡参数待修正，暂无法核对报告。');caution=True
        elif visible:
            if not part_matches:messages.append('报告器件与当前板卡型号不同，以下数值仅供查看。');caution=True
            elif kind=='build':
                if self.manifest.get('board_fingerprint')!=board_fingerprint(self.board):messages.append('板卡设置已变化：此报告对应旧配置。');caution=True
                else:messages.append('构建配置与当前设置一致。')
                if self.manifest.get('source_fingerprint')!=self.current_sources:messages.append('构建源码与当前程序不同，请重新综合以更新占用。');caution=True
            elif kind=='reference':
                reference_board=Board.from_dict(json.loads((self.reference/'board.json').read_text(encoding='utf-8')))
                messages.append('参考固件：4 路 DAC / 10 bit / 50 MHz 单端。')
                if board_fingerprint(reference_board)!=board_fingerprint(self.board):messages.append('参考配置与当前板卡设置不同。');caution=True
            else:messages.append('仅核对了器件型号，导入报告未关联当前工程参数。')
            messages.append('占用来自报告；未通过串口确认板上固件。')
        if self.manifest:
            if self.manifest.get('status')!='success':messages.append('该次构建未成功，已有统计不能作为构建通过依据。');caution=True
            elif not report:messages.append('该次仅生成工程，尚无综合资源统计。' if self.manifest.get('mode')=='generate' else '该次构建没有可核验的资源报告。')
            if self.manifest.get('sources_changed'):messages.append('构建期间源码发生变化，需重新构建。');caution=True
            if self.manifest.get('warnings'):messages.extend(self.manifest['warnings']);caution=True
        if self.error:messages.append(self.error);caution=True
        self.state.setText(' '.join(messages) or '物理资源由固件设计决定，波形下发不会重新分配 LUT / BRAM。')
        self.state.setStyleSheet(f'font-size:11px;color:{"#a46625" if caution else "#647c9e"};')
        summary_metrics={}
        for key,widgets in self.metrics.items():
            metric=visible.get('metrics',{}).get(key) if visible else None;metric=metric or {}
            used,available,percent=(metric.get(name) for name in ('used','available','percent'))
            color='#b43f48' if percent is not None and percent>100 else '#c18631' if caution else '#2563eb'
            raw=metric.get('raw_percent','').strip().rstrip('%')
            widgets['percent'].setText('—' if percent is None else raw+'%' if raw.startswith(('<','>')) else f'{percent:.2f}%')
            widgets['percent'].setStyleSheet(f'font-size:19px;font-weight:600;color:{color};')
            widgets['count'].setText(f'{number(used)} / {number(available)}')
            widgets['bar'].setValue(0 if percent is None else min(1000,max(0,round(percent*10))))
            widgets['bar'].setStyleSheet(f'QProgressBar{{border:0;border-radius:2px;background:#e3ebf8;}} QProgressBar::chunk{{border-radius:2px;background:{color};}}')
            summary_metrics[key]=dict(text=widgets['percent'].text(),count=widgets['count'].text(),value=widgets['bar'].value(),color=color)
        details=[]
        if report:
            details=[f'来源：{source}',f'报告文件：{report["path"]}',f'设计：{report.get("design") or "未提供"}',f'工具：{report.get("tool") or "未提供"}',f'SHA256：{report["sha256"]}',*report.get('warnings',[])]
        if self.manifest:details.extend([f'构建状态：{self.manifest.get("status")}',f'构建模式：{self.manifest.get("mode")}',f'关联文件：{self.manifest.get("manifest_path")}'])
        if self.manifest and self.manifest.get('board_context'):
            context=self.manifest['board_context']
            details.append('构建配置：'+json.dumps(context,ensure_ascii=False))
        details.append('BRAM 按 36 Kb 块等效计数，0.5 表示半块；总量不等于波形可下载的剩余容量。')
        self.details_label.setText('\n'.join(details));self.open_action.setEnabled(bool(report))
        exceeded=any(m and m.get('percent') is not None and m['percent']>100 for m in (visible or {}).get('metrics',{}).values())
        self.summary=dict(metrics=summary_metrics,
            source=({'build':'本机构建','import':'导入报告','reference':'发布参考'}.get(kind,'资源报告')+' · '+self.stage.text()) if visible else '暂无匹配的资源报告',
            status='资源超限 · 查看详情' if exceeded else '报告需核对 · 查看详情' if caution else '报告占用 · 未核对板上固件' if visible else '点击查看生成与导入方式',
            color='#b43f48' if exceeded else '#a46625' if caution else '#647c9e',
            tooltip=self.origin.text()+'\n'+self.state.text())
        self.changed.emit()


class UtilizationSummary(QFrame):
    """Always visible below the scrolling navigation, without a second cache."""
    details_requested=Signal()

    def __init__(self,panel):
        super().__init__();self.panel=panel;self.setObjectName('resourceSummary')
        self.setStyleSheet('QFrame#resourceSummary{background:#f8fbff;border:1px solid #dbe7fa;border-radius:14px;}')
        box=QVBoxLayout(self);box.setContentsMargins(10,8,10,10);box.setSpacing(7)
        self.open_button=SoftButton('硬件资源  ›');self.open_button.setAccessibleName('查看 FPGA 硬件资源详情')
        self.open_button.setStyleSheet('QPushButton{border:0;background:transparent;text-align:left;padding:2px 0;font-size:12px;font-weight:600;color:#294f7d;} QPushButton:hover{color:#2563eb;} QPushButton:focus{background:#e5eeff;border-radius:6px;}')
        self.open_button.clicked.connect(self.details_requested.emit);box.addWidget(self.open_button)
        self.source=QLabel();self.source.setStyleSheet('font-size:10px;color:#647c9e;');box.addWidget(self.source)
        grid=QGridLayout();grid.setContentsMargins(0,0,0,0);grid.setHorizontalSpacing(10);grid.setVerticalSpacing(8);box.addLayout(grid)
        self.metrics={}
        for i,(key,title) in enumerate((('lut','LUT'),('ff','FF'),('bram','BRAM'),('dsp','DSP'))):
            cell=QWidget();v=QVBoxLayout(cell);v.setContentsMargins(0,0,0,0);v.setSpacing(4)
            name=QLabel(title);name.setStyleSheet('font-size:10px;color:#647c9e;');v.addWidget(name)
            percent=QLabel('—');v.addWidget(percent)
            bar=QProgressBar();bar.setRange(0,1000);bar.setFixedHeight(4);bar.setTextVisible(False)
            bar.setSizePolicy(QSizePolicy.Ignored,QSizePolicy.Fixed);v.addWidget(bar)
            grid.addWidget(cell,i//2,i%2);grid.setColumnStretch(i%2,1)
            self.metrics[key]=dict(percent=percent,bar=bar,cell=cell)
        self.state=QLabel();self.state.setWordWrap(True);box.addWidget(self.state)
        panel.changed.connect(self.refresh);self.refresh()

    def refresh(self):
        snapshot=self.panel.summary
        self.source.setText(snapshot['source']);self.setToolTip(snapshot['tooltip'])
        self.state.setText(snapshot['status']);self.state.setStyleSheet(f'font-size:10px;color:{snapshot["color"]};')
        for key,widgets in self.metrics.items():
            data=snapshot['metrics'][key];color=data['color']
            widgets['percent'].setText(data['text']);widgets['percent'].setStyleSheet(f'font-size:13px;font-weight:600;color:{color};')
            widgets['bar'].setValue(data['value']);widgets['cell'].setToolTip(data['count']+(' · 36 Kb 等效' if key=='bram' else '')+'\n'+snapshot['tooltip'])
            widgets['bar'].setStyleSheet(f'QProgressBar{{border:0;border-radius:2px;background:#e3ebf8;}} QProgressBar::chunk{{border-radius:2px;background:{color};}}')
