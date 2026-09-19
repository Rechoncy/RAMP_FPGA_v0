"""Flash configuration, persistent job logs, and explicit programming actions."""
import datetime,json,sys
from pathlib import Path
from PySide6.QtCore import QProcess,QTimer,Signal,QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QWidget,QVBoxLayout,QHBoxLayout,QFormLayout,QLabel,QLineEdit,QComboBox,QCheckBox,QPlainTextEdit,QFileDialog,QScrollArea
from .appearance import SoftButton
from .flash import FlashSettings
from .runtime import frozen


class FlashPage(QWidget):
    busy_changed=Signal(bool)

    def __init__(self,board_page):
        super().__init__();self.board_page=board_page;self.process=None;self.serial_connected=False;self.job_dir=None;self.log_offset=0
        self.timer=QTimer(self);self.timer.setInterval(150);self.timer.timeout.connect(self.read_log)
        outer=QVBoxLayout(self);scroll=QScrollArea();scroll.setWidgetResizable(True);outer.addWidget(scroll)
        content=QWidget();scroll.setWidget(content);layout=QVBoxLayout(content);layout.setSpacing(12)
        hint=QLabel('Flash 可同时固化 FPGA 固件与上电波形。“准备”或“仅下载方案”写入 RAM；需要掉电保留时，在下方纳入上电方案、重新构建 .bit，再固化并重启。');hint.setWordWrap(True);layout.addWidget(hint)
        def section(text):
            heading=QLabel(text);heading.setObjectName('section');layout.addWidget(heading)
        section('1 · 准备上电方案')
        self.boot_profiles=dict(board_page.board.boot_profiles)
        self.boot_summary=QLabel();self.boot_summary.setWordWrap(True);layout.addWidget(self.boot_summary)
        from .output_state import OutputState
        self.selection_path=board_page.library.root/'flash_selection.json'
        selection=OutputState.read(self.selection_path,{})
        if not self.selection_path.exists():
            old=OutputState.read(board_page.library.root/'parallel.json',{})
            selection={str(i):(str(i) in self.boot_profiles if self.boot_profiles else old.get(str(i),{}).get('enabled',True)) for i in range(8)}
            OutputState.write(self.selection_path,selection)
        self.boot_checks=[];select_row=QHBoxLayout()
        for ch in range(8):
            check=QCheckBox(f'DAC {ch+1}');check.setChecked(selection.get(str(ch),True));select_row.addWidget(check);self.boot_checks.append(check)
            check.toggled.connect(self.save_selection)
        select_row.addStretch();layout.addLayout(select_row)
        board_page.dac_count.valueChanged.connect(self.refresh_selection);self.refresh_selection()
        boot_row=QHBoxLayout()
        for text,callback in [('纳入上方所选方案',self.capture_boot),('清空上电方案',self.clear_boot),('进入构建',lambda:board_page.tabs.setCurrentWidget(board_page.build_page))]:
            b=SoftButton(text);b.clicked.connect(callback);boot_row.addWidget(b)
        layout.addLayout(boot_row);self.show_boot()
        scope=QLabel('上方勾选仅用于 Flash；与输出控制的临时操作范围独立。纳入各 DAC 本地方案及触发设置，未选择方案的通道跳过。');scope.setWordWrap(True);scope.setObjectName('subtitle');layout.addWidget(scope)
        section('2 · Flash 参数')
        row=QHBoxLayout();preset=SoftButton('填入本机已验证 Flash 参数');preset.clicked.connect(self.preset);row.addWidget(preset)
        row.addStretch();layout.addLayout(row)
        form=QFormLayout();form.setVerticalSpacing(12);self.part=QComboBox();self.part.setEditable(True);self.part.addItem('')
        self.part.setToolTip('Vivado 配置存储器型号；扫描 JTAG 不等于识别实际 Flash 型号')
        self.size=QComboBox()
        for n in (1,2,4,8,16,32,64,128,256):self.size.addItem(f'{n} MiB / {n*8} Mbit',n)
        self.interface=QComboBox();self.interface.addItems(['SPIx1','SPIx2','SPIx4'])
        self.rate=QComboBox()
        for n in (3,6,9,12,16,22,26,33,40,50,66):self.rate.addItem(f'{n} MHz',n)
        self.falling=QCheckBox('Flash 启动时钟使用下降沿');self.backup=QCheckBox('固化前读取并保存整片备份（默认开启）')
        form.addRow('Flash 型号',self.part);form.addRow('容量（MiB ≠ Mbit）',self.size)
        row=QHBoxLayout();row.addWidget(self.interface);row.addWidget(QLabel('配置时钟'));row.addWidget(self.rate);form.addRow('SPI 位宽',row)
        form.addRow(self.falling);form.addRow('启动地址',QLabel('0x00000000 · 单镜像启动'))
        layout.addLayout(form);section('3 · JTAG 与待固化文件');form=QFormLayout();form.setVerticalSpacing(12)
        self.server=QLineEdit();self.target=QComboBox();self.target.setEditable(True);self.device=QComboBox();self.device.setEditable(True)
        form.addRow('硬件服务器',self.server);form.addRow('JTAG 目标',self.target);form.addRow('FPGA 器件',self.device)
        self.bit=QLineEdit();browse=SoftButton('选择 .bit');browse.clicked.connect(self.browse)
        row=QHBoxLayout();row.addWidget(self.bit,1);row.addWidget(browse);form.addRow('待固化比特流',row)
        self.expected=QLineEdit();self.expected.setPlaceholderText('可选，例如 09112040；留空只检查 DONE / CRC')
        form.addRow('预期固件 USR_ACCESS',self.expected);form.addRow(self.backup);layout.addLayout(form)
        note=QLabel('SPI 位宽与配置时钟只对重新构建的比特流生效；把已有 .bit 转为 MCS 不会改写其中的启动设置。这里的配置时钟独立于 DAC 输出采样时钟。需将板上启动跳线设为相应 Master SPI 模式。');note.setWordWrap(True);note.setObjectName('subtitle');layout.addWidget(note)
        note=QLabel('Flash 擦写会替换地址 0 的启动镜像，并临时把 FPGA 配置为烧写辅助电路，DAC 输出会中断。固化期间不要断电；成功后点击“从 Flash 重启”。执行 JTAG 操作前请断开上位机串口。');note.setWordWrap(True);note.setObjectName('notice');layout.addWidget(note)
        section('4 · 执行与验证')
        self.buttons={}
        for actions in ([('scan','扫描 JTAG / 型号库'),('generate','生成 MCS / BIN'),('temporary','临时下载 .bit')],
                        [('program','备份并固化 + 校验'),('boot','从 Flash 重启'),('status','只读检查启动状态')]):
            row=QHBoxLayout()
            for op,title in actions:
                b=SoftButton(title);b.clicked.connect(lambda checked=False,key=op:self.start(key));row.addWidget(b);self.buttons[op]=b
            layout.addLayout(row)
        self.buttons['program'].setObjectName('primary');self.backup.toggled.connect(self.refresh)
        self.feedback=QLabel('设置保存在板卡 JSON；操作结果、备份与日志保存在 user_data/flash_jobs。');self.feedback.setWordWrap(True);layout.addWidget(self.feedback)
        self.log=QPlainTextEdit();self.log.setReadOnly(True);self.log.setMinimumHeight(210);self.log.setMaximumBlockCount(2500);layout.addWidget(self.log)
        folder=SoftButton('打开本次输出 / 备份目录');folder.clicked.connect(self.open_folder);layout.addWidget(folder)
        self.load(board_page.board.flash)

    def show_boot(self):
        lines=['上电方案快照（下次构建生效；保存设置本身不会写入 Flash）']
        for key,p in sorted(self.boot_profiles.items()):
            if p.get('mode')=='scan':
                lines.append(f'DAC {int(key)+1} · {p["name"]} · 触发步进 {p["scan_points"]} 点 · 起始 {p["scan_start"]} / 步长 {p["scan_step"]:+d} · 上电从第 1 点等待触发');continue
            if p.get("mode")=="sequence":
                repeat=' · 无限整表重复' if p.get('repeat')==0 else ''
                lines.append(f'DAC {int(key)+1} · {p["name"]} · {len(p["sequence_steps"])} 步 · 初始 {p["initial"]}{repeat} · 上电恢复完整序列');continue
            lines.append(f'DAC {int(key)+1} · {p["name"]} · 初始 {p["initial"]} → 目标 {p["amplitude"]} · '+('外部触发，上电自动布防' if p['trigger']=='external' else '软件触发，上电等待软件命令'))
        lines.append('未纳入通道：上电保持 DAC 数字中点，不执行波形。')
        self.boot_summary.setText('\n'.join(lines))

    def capture_boot(self):
        try:
            from .boot import snapshot_assignments
            from .model import Profile,Capabilities,compile_profile
            page=self.board_page
            profiles=snapshot_assignments(page.library,page.dac_count.value(),[i for i,c in enumerate(self.boot_checks) if c.isChecked()])
            caps=Capabilities(core_hz=page.output_rate.currentData(),dac_bits=page.dac_bits.currentData(),dac_count=page.dac_count.value(),trigger_count=page.trigger_count.value(),parallel_channels=True,sequence_steps=64,infinite_repeat=True,trigger_scan=True,interpolation=True)
            for p in profiles.values():compile_profile(Profile.from_dict(p),caps)
            self.boot_profiles=profiles;self.show_boot()
            if not page.save():
                self.feedback.setText('快照已进入编辑状态，但板卡配置保存失败；请修正参数后保存。');return
            self.feedback.setText('已纳入保存版本。完整离散和容量校验在构建时执行；修改方案后需重新纳入、构建和固化。')
        except Exception as exc:self.feedback.setText(f'未纳入：{exc}')

    def save_selection(self):
        from .output_state import OutputState
        OutputState.write(self.selection_path,{str(i):c.isChecked() for i,c in enumerate(self.boot_checks)})

    def refresh_selection(self):
        for i,c in enumerate(self.boot_checks):c.setVisible(i<self.board_page.dac_count.value())

    def clear_boot(self):
        self.boot_profiles={};self.show_boot()
        saved=self.board_page.save()
        self.feedback.setText('已清空下次构建的上电方案。当前板卡与 Flash 内容尚未改变。' if saved else '已清空编辑中的快照，但板卡配置保存失败；请修正参数后保存。')

    def settings(self):
        cfg=FlashSettings(cfgmem_part=self.part.currentText().strip(),size_mib=self.size.currentData(),interface=self.interface.currentText(),
                          config_rate=self.rate.currentData(),falling_edge=self.falling.isChecked(),server=self.server.text().strip(),
                          target=self.target.currentText().strip(),device=self.device.currentText().strip(),bitfile=self.bit.text().strip(),
                          expected_usr_access=self.expected.text().strip(),backup=self.backup.isChecked())
        cfg.validate();return cfg

    def load(self,data):
        cfg=FlashSettings.from_dict(data);cfg.validate()
        self.part.setCurrentText(cfg.cfgmem_part);self.size.setCurrentIndex(self.size.findData(cfg.size_mib));self.interface.setCurrentText(cfg.interface)
        self.rate.setCurrentIndex(self.rate.findData(cfg.config_rate));self.falling.setChecked(cfg.falling_edge);self.server.setText(cfg.server)
        self.target.setCurrentText(cfg.target);self.device.setCurrentText(cfg.device);self.bit.setText(cfg.bitfile)
        self.expected.setText(cfg.expected_usr_access);self.backup.setChecked(cfg.backup);self.refresh()

    def preset(self):
        cfg=self.settings();cfg.cfgmem_part='mt25ql128-spi-x1_x2_x4';cfg.size_mib=16;cfg.interface='SPIx1';cfg.config_rate=16;cfg.falling_edge=True
        self.load(cfg.to_dict());self.feedback.setText('填入此前已验证的 Micron 128 Mbit / SPIx1 参数。迁移到其他板卡时请使用该板的实际 Flash 型号。尚未保存或烧写。')

    def browse(self):
        path,_=QFileDialog.getOpenFileName(self,'选择 FPGA 比特流',self.bit.text(),'Bitstream (*.bit)')
        if path:self.bit.setText(path)

    def running(self):return self.process is not None and self.process.state()!=QProcess.NotRunning

    def refresh(self):
        if not hasattr(self,'buttons'):return
        building=self.board_page.process is not None and self.board_page.process.state()!=QProcess.NotRunning
        for op,b in self.buttons.items():b.setEnabled(not self.running() and not building and (op=='generate' or not self.serial_connected))
        self.buttons['program'].setText('备份并固化 + 校验' if self.backup.isChecked() else '固化 + 校验（不备份）')

    def start(self,operation):
        self.refresh()
        if not self.buttons[operation].isEnabled():return
        try:
            cfg=self.settings();cfg.validate(operation)
            vivado=self.board_page.vivado.text().strip()
            if not Path(vivado).is_file():raise ValueError('请在“板卡信息”中设置有效 Vivado 路径')
            self.job_dir=self.board_page.library.root/'flash_jobs'/datetime.datetime.now().strftime('%Y%m%d_%H%M%S_%f')
            self.job_dir.mkdir(parents=True,exist_ok=False)
            job=self.job_dir/'job.json';job.write_text(json.dumps({'operation':operation,'settings':cfg.to_dict(),'vivado':vivado},ensure_ascii=False,indent=2),encoding='utf-8')
            self.log_path=self.job_dir/'worker.log';self.log_offset=0;self.log.clear();self.feedback.setText(f'执行中：{self.job_dir}')
            self.process=QProcess(self);self.process.setWorkingDirectory(str(Path(__file__).resolve().parents[1]));self.process.setProcessChannelMode(QProcess.MergedChannels)
            self.process.readyReadStandardOutput.connect(self.read_log);self.process.finished.connect(self.finished);self.process.errorOccurred.connect(self.failed)
            python=Path(sys.executable)
            if frozen():args=['--flash-worker','--worker-log',str(self.log_path),'--job',str(job)]
            else:
                if python.name.lower()=='pythonw.exe':python=python.with_name('python.exe')
                args=['-u','-m','host.flash_worker','--job',str(job)]
            self.process.start(str(python),args);self.timer.start();self.board_page.build_button.setEnabled(False);self.busy_changed.emit(True);self.refresh()
        except Exception as exc:self.feedback.setText(f'未执行：{exc}')

    def read_log(self):
        if not self.process:return
        text=bytes(self.process.readAllStandardOutput()).decode('utf-8',errors='replace')
        if frozen() and self.log_path.exists():
            import codecs
            with self.log_path.open('rb') as f:f.seek(self.log_offset);raw=f.read()
            decoder=codecs.getincrementaldecoder('utf-8')(errors='replace');text+=decoder.decode(raw,final=False);self.log_offset+=len(raw)-len(decoder.getstate()[0])
        elif text:
            with self.log_path.open('a',encoding='utf-8') as f:f.write(text)
        cursor=self.log.textCursor();cursor.movePosition(cursor.MoveOperation.End);cursor.insertText(text);self.log.setTextCursor(cursor)

    def finished(self,code,status):
        self.timer.stop();self.read_log();self.board_page.build_button.setEnabled(True);self.busy_changed.emit(False);self.refresh()
        report=self.job_dir/'result.json'
        result=json.loads(report.read_text(encoding='utf-8')) if report.exists() else {}
        passed=code==0 and result.get('passed',False)
        if result.get('operation')=='scan':
            old=self.part.currentText();self.part.clear();self.part.addItem('');self.part.addItems(result.get('parts',[]));self.part.setCurrentText(old)
            targets=result.get('targets',[])
            for combo,key in ((self.target,'target'),(self.device,'device')):
                old=combo.currentText();values=list(dict.fromkeys(r[key] for r in targets));combo.clear();combo.addItems(values)
                if old in values or len(values)!=1:combo.setCurrentText(old)
        self.feedback.setText(('完成' if passed else '失败或结果未确认，请查看日志')+f' · {self.job_dir}')
        if passed and result.get('operation')=='program':self.feedback.setText('Flash 写入与逐字节校验通过。FPGA 仍为烧写辅助配置，请点击“从 Flash 重启”；完整断电重启仍需单独验证。')

    def failed(self,error):
        if error==QProcess.FailedToStart:
            self.timer.stop();self.board_page.build_button.setEnabled(True);self.busy_changed.emit(False);self.refresh();self.feedback.setText('无法启动 Flash 工作进程')

    def open_folder(self):
        folder=self.job_dir or self.board_page.library.root/'flash_jobs';folder.mkdir(parents=True,exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder)))
