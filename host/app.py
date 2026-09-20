from __future__ import annotations
import copy
from dataclasses import replace
import json
import sys
import uuid
from pathlib import Path
from PySide6.QtCore import Qt, QThread, Signal, QTimer, QProcess, QSettings, QUrl
from PySide6.QtGui import QDesktopServices, QCloseEvent, QFontDatabase, QFont, QIcon
from PySide6.QtWidgets import (QApplication,QMainWindow,QWidget,QFrame,QVBoxLayout,QHBoxLayout,
    QLabel,QPushButton,QStackedWidget,QListWidget,QListWidgetItem,QLineEdit,QComboBox,QSpinBox,
    QDoubleSpinBox,QTableWidget,QTableWidgetItem,QHeaderView,QAbstractItemView,QFormLayout,QLayout,
    QGridLayout,QMessageBox,QFileDialog,QPlainTextEdit,QSplitter,QDialog,QDialogButtonBox,QScrollArea,QBoxLayout,QTabWidget)
from .theme import STYLE
from .appearance import SoftButton, SoftStack, FieldGrid, fit_combo
from .model import Profile,Segment,Capabilities,compile_profile,fingerprint
from .storage import Library
from .worker import DeviceWorker
from .protocol import ARM,TRIGGER,ABORT,DISARM,STATES
from .plot import WaveformPlot
from .board_page import BoardPage
from .debug_page import DebugPage
from .trigger_debug_page import TriggerDebugPage
from .parallel_page import ParallelPage
from .dac_navigation import DacNavigation
from .channel_page import ChannelPage
from .time_input import TimeInput,display_time
from .resource_monitor import ResourceMonitor
from .resource_view import ResourceUsageView
from .utilization_panel import UtilizationSummary
from .session import WorkspaceSession
from .uart_settings import BAUD_RATES
from .workspace_transfer import WorkspaceTransfer
from .boot import capacity


def label(text, name=None):
    w=QLabel(text)
    if name: w.setObjectName(name)
    return w


def button(text, action, name=None):
    w=SoftButton(text); w.clicked.connect(action)
    if name: w.setObjectName(name)
    return w


def card():
    f=QFrame(); f.setObjectName("card"); v=QVBoxLayout(f); v.setContentsMargins(20,18,20,18); v.setSpacing(11)
    return f,v


class Studio(WorkspaceTransfer,DacNavigation,QMainWindow):
    request=Signal(str,object)
    hardware_model=Signal(str)
    workspace_reopen=Signal(str,str)

    def __init__(self, library=None):
        super().__init__()
        # Offscreen Qt on Windows has no font registry. Load the installed fonts
        # explicitly so visual tests use the same Chinese glyphs as the desktop.
        if not QFontDatabase.families():
            for name in ("msyh.ttc","msyhbd.ttc","segoeui.ttf","consola.ttf"):
                font=Path("C:/Windows/Fonts")/name
                if font.exists(): QFontDatabase.addApplicationFont(str(font))
            QApplication.instance().setFont(QFont("Microsoft YaHei UI",10))
        self.library=library or Library()
        self.session=WorkspaceSession(self.library.root);self._exit_saved=False;self._closing_disconnect=False;self._workspace_reopen=None
        self.setWindowTitle("Ramp Studio · FPGA 波形控制")
        self.setWindowIcon(QIcon(str(Path(__file__).parent / "assets" / "ramp-studio.ico")))
        self.resize(1440,1000); self.setMinimumSize(1080,700)
        from .runtime import frozen
        self.settings=(QSettings(str(self.library.root/"desktop.ini"),QSettings.IniFormat)
                       if frozen() else QSettings("RampStudio","Desktop"))
        self.profile=Profile(mode="ramp",initial=512,safe=512); self.legacy_readonly=False; self.saved_fingerprint=""; self.uploaded_fingerprint=""
        self.uploading_profile=None; self.uploaded_id=None; self.profile_valid=True
        self.connected=False; self.demo=False; self.busy=False; self.loading=False
        self.caps=Capabilities(); self.last_status=None; self.channel_statuses={}; self.channel_uploads={}; self.dac_drafts={}; self.dac_buttons=[]; self.active_dac=0
        self.resource_monitor=ResourceMonitor(self);self.resource_monitor.changed.connect(self.refresh_resources)
        self.resource_monitor.stopped.connect(self.close)
        self.worker=DeviceWorker(); self.thread=QThread(self); self.worker.moveToThread(self.thread)
        self.hardware_model.connect(self.worker.set_dac_model)
        self.request.connect(self.worker.execute); self.worker.result.connect(self.worker_result)
        self.worker.failed.connect(self.worker_failed); self.worker.status.connect(self.on_status); self.worker.progress.connect(self.notice)
        self.worker.result.connect(self.finish_close)
        self.worker.applied.connect(self.worker_applied)
        self.worker.capabilities.connect(self.on_capabilities);self.worker.resources.connect(self.on_resource_reports)
        self.thread.finished.connect(self.worker.deleteLater); self.thread.start()
        base=QWidget(); base.setObjectName("workspace"); main=QHBoxLayout(base); main.setContentsMargins(12,12,12,4); main.setSpacing(0)
        side=QFrame(); side.setObjectName("sidebar"); side.setFixedWidth(204); nav=QVBoxLayout(side); nav.setContentsMargins(16,24,16,20); nav.setSpacing(10)
        nav.addWidget(label("RAMP", "brand")); nav.addWidget(label("S T U D I O", "subtitle")); nav.addSpacing(14)
        self.stack=SoftStack(); self.nav_buttons={}
        menu_scroll=QScrollArea(); menu_scroll.setWidgetResizable(True); menu_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        menu=QWidget(); menu_layout=QVBoxLayout(menu); menu_layout.setContentsMargins(0,0,0,0); menu_layout.setSpacing(6)
        for i,text in [(1,"板卡与连接"),(0,"方案设计与保存")]:
            b=button(text,lambda checked=False,index=i:self.navigate(index),"nav"); b.setCheckable(True); menu_layout.addWidget(b); self.nav_buttons[i]=b
        menu_layout.addSpacing(12)
        b=button('输出控制',lambda:self.navigate(4),'nav');b.setCheckable(True);menu_layout.addWidget(b);self.nav_buttons[4]=b
        self.dac_nav_layout=QVBoxLayout(); self.dac_nav_layout.setSpacing(7); menu_layout.addLayout(self.dac_nav_layout)
        menu_layout.addSpacing(14); menu_layout.addWidget(label("控制与管理","subtitle"))
        for i,text in [(3,"系统调试"),(2,"运行记录")]:
            b=button(text,lambda checked=False,index=i:self.navigate(index),"nav"); b.setCheckable(True); menu_layout.addWidget(b); self.nav_buttons[i]=b
        menu_layout.addStretch(); menu_scroll.setWidget(menu); nav.addWidget(menu_scroll,1)
        self.sidebar_footer=label("200 MHz\nFPGA waveform control");nav.addWidget(self.sidebar_footer); nav.addSpacing(10)
        self.workspace_actions=QWidget();workspace_row=QHBoxLayout(self.workspace_actions)
        workspace_row.setContentsMargins(0,0,0,0);workspace_row.setSpacing(6)
        self.workspace_save_button=button('全局保存',self.choose_save_workspace)
        self.workspace_import_button=button('全局导入',self.choose_import_workspace)
        self.workspace_save_button.setToolTip('保存板卡、引脚、Flash 设置、方案库、编辑草稿和 DAC 分配为一个配置包。')
        self.workspace_import_button.setToolTip('导入配置与方案；先备份当前工作区，恢复后保持未连接。')
        for b in (self.workspace_save_button,self.workspace_import_button):
            b.setMinimumWidth(0);b.setMinimumHeight(34);b.setStyleSheet('padding:7px 4px;font-size:12px;')
            workspace_row.addWidget(b,1)
        nav.addWidget(self.workspace_actions)
        self.side_status=label("●  未连接"); nav.addWidget(self.side_status)
        main.addWidget(side)
        content=QVBoxLayout(); content.setContentsMargins(22,12,10,8); content.addWidget(self.stack); main.addLayout(content,1)
        self.make_workspace()
        self.board_page=BoardPage(self.library); self.board_page.notification.connect(self.notice); self.board_page.configuration_changed.connect(self.refresh_hardware_options); self.board_page.layout().insertWidget(2,self.connect_card); self.stack.addWidget(self.board_page)
        self.session.restore_board(self)
        self.board_page.tabs.currentChanged.connect(self.sync_connection_panel)
        self.board_page.flash_page.busy_changed.connect(lambda busy:self.update_buttons())
        self.board_page.busy_changed.connect(lambda busy:self.update_buttons())
        self.hardware_resources=UtilizationSummary(self.board_page.utilization_panel)
        nav.insertWidget(nav.indexOf(self.sidebar_footer),self.hardware_resources)
        self.hardware_resources.details_requested.connect(self.show_hardware_resources)
        self.resource_dialog=QDialog(self);self.resource_dialog.setWindowTitle('FPGA 硬件资源 · 报告详情')
        self.resource_dialog.resize(840,580);self.resource_dialog.setMinimumSize(600,440)
        resource_box=QVBoxLayout(self.resource_dialog);resource_box.setContentsMargins(20,20,20,16);resource_box.setSpacing(12)
        self.resource_scroll=QScrollArea();self.resource_scroll.setWidgetResizable(True)
        self.resource_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.resource_scroll.setWidget(self.board_page.utilization_panel);self.board_page.utilization_panel.show()
        resource_box.addWidget(self.resource_scroll,1)
        resource_actions=QDialogButtonBox(QDialogButtonBox.Close);resource_actions.button(QDialogButtonBox.Close).setText('关闭')
        resource_actions.rejected.connect(self.resource_dialog.hide);resource_box.addWidget(resource_actions)
        self.board_page.utilization_panel.build_requested.connect(self.navigate_resource_build)
        self.make_history()
        self.debug_page=DebugPage();self.trigger_debug_page=TriggerDebugPage()
        self.debug_tabs=QTabWidget();self.debug_tabs.addTab(self.debug_page,'静态原码')
        self.trigger_debug_scroll=QScrollArea();self.trigger_debug_scroll.setWidgetResizable(True);self.trigger_debug_scroll.setWidget(self.trigger_debug_page)
        self.debug_tabs.addTab(self.trigger_debug_scroll,'触发调试');self.stack.addWidget(self.debug_tabs)
        self.trigger_debug_page.requested.connect(lambda p:self.send('trigger_debug',p))
        self.worker.trigger_status.connect(self.trigger_debug_page.readback)
        self.debug_page.workspace_requested.connect(lambda:self.navigate(0))
        self.debug_page.connection_requested.connect(lambda:self.navigate(1))
        self.debug_page.output_requested.connect(self.set_static_output)
        self.debug_page.command_requested.connect(self.command)
        self.parallel_page=ParallelPage(self.library); self.parallel_page.operation.connect(self.parallel_operation); self.stack.addWidget(self.parallel_page)
        self.parallel_page.resource_provider=self.resource_usage;self.parallel_page.resource_source=self.resource_source
        self.parallel_page.debug_requested.connect(self.open_debug)
        self.parallel_page.channel_requested.connect(self.open_dac)
        self.channel_page=ChannelPage(self); self.stack.addWidget(self.channel_page)
        for combo in self.findChildren(QComboBox):fit_combo(combo)
        self.setCentralWidget(base); self.setStyleSheet(STYLE)
        self.navigate(0); self.refresh_library()
        current=[p for p in self.library.list() if p.mode in ('ramp','function')]
        bits=self.board_page.dac_bits.currentData();mid=1<<(bits-1)
        self.load_profile(next((p for p in current if p.mode=='ramp'),current[0] if current else Profile(mode='ramp',name='新实验方案',dac_bits=bits,initial=mid,safe=mid,amplitude=mid-400)))
        self.refresh_ports(); self.statusBar().showMessage("就绪 · 预览为数字码计算结果，不是模拟输出实测")
        geometry=self.settings.value("geometry")
        if geometry: self.restoreGeometry(geometry)
        self.restore_dac_session();self.session.restore_editors(self);self.update_buttons();self.session.restore_view(self)

    def show_hardware_resources(self):
        self.board_page.refresh_utilization_context()
        self.resource_dialog.show();self.resource_dialog.raise_();self.resource_dialog.activateWindow()

    def navigate_resource_build(self):
        self.resource_dialog.hide();self.navigate(1)

    def make_workspace(self):
        page=QWidget(); outer=QVBoxLayout(page); outer.setContentsMargins(0,0,0,0); outer.setSpacing(15)
        title=QHBoxLayout(); tv=QVBoxLayout(); self.workspace_title=label("方案设计与保存","title"); tv.addWidget(self.workspace_title); tv.addWidget(label("设计波形 → 保存方案 → 在 DAC 页面选择并准备","subtitle")); title.addLayout(tv); title.addStretch()
        outer.addLayout(title)
        connect_card,cv=card(); self.connect_card=connect_card
        header=QHBoxLayout(); header.addWidget(label("设备连接","section")); header.addStretch()
        self.connection_toggle=button('收起连接设置',self.toggle_connection_panel);header.addWidget(self.connection_toggle)
        self.connection_badge=label("OFFLINE","badge"); header.addWidget(self.connection_badge); cv.addLayout(header)
        self.connection_controls=QWidget();connections=QVBoxLayout(self.connection_controls);connections.setContentsMargins(0,0,0,0)
        row=QHBoxLayout(); row.addWidget(label("接口 / 设备"))
        self.ports=QComboBox(); self.ports.setMinimumWidth(225); row.addWidget(self.ports,1)
        self.baudrate_label=label('波特率');row.addWidget(self.baudrate_label)
        self.baudrate=QComboBox()
        for rate in BAUD_RATES:self.baudrate.addItem('1,000,000 · 1M' if rate==1_000_000 else f'{rate:,}',rate)
        self.baudrate.setCurrentIndex(self.baudrate.findData(115200));self.baudrate.setMinimumWidth(132)
        self.baudrate.setToolTip('新版固件连接时自动适配所选速率；先断开再修改。旧固定速率固件仍须匹配其烧录设置。')
        row.addWidget(self.baudrate)
        self.ports.currentIndexChanged.connect(self.sync_transport_controls)
        row.addWidget(button("刷新",self.refresh_ports)); self.connect_button=button("连接设备",self.toggle_connect,"primary"); row.addWidget(self.connect_button)
        self.hardware_summary=label("200 MHz 核心  /  200 MHz DAC  /  10 bit","subtitle"); self.hardware_summary.setWordWrap(True); connections.addLayout(row); connections.addWidget(self.hardware_summary);cv.addWidget(self.connection_controls)
        resource_row=QHBoxLayout();self.capacity_summary=label('', 'subtitle');self.capacity_summary.setWordWrap(True);resource_row.addWidget(self.capacity_summary,1)
        self.capacity_summary.setToolTip('串口读取已加载固件实际开放的容量；每路独立，双缓冲不相加。芯片型号及 LUT / FF / BRAM 总占用需查看 Vivado 报告。')
        self.capacity_refresh=button('刷新能力',lambda:self.send('capabilities',None));resource_row.addWidget(self.capacity_refresh);connections.addLayout(resource_row)
        body=QHBoxLayout(); body.setSpacing(16); self.workspace_body=body
        library,lv=card(); self.library_card=library; library.setFixedWidth(230); lv.addWidget(label("方案库","section"))
        self.search=QLineEdit(); self.search.setPlaceholderText("搜索方案…"); self.search.textChanged.connect(self.filter_library); lv.addWidget(self.search)
        self.profile_list=QListWidget(); self.profile_list.currentItemChanged.connect(self.choose_profile)
        self.compact_library,compact=card();self.compact_library.hide();compact_row=QHBoxLayout()
        compact_row.addWidget(label('波形方案'));self.profile_picker=fit_combo(QComboBox());compact_row.addWidget(self.profile_picker,1)
        compact_row.addWidget(button('新建',self.new_profile))
        self.library_toggle=button('管理方案',self.arrange_actions);self.library_toggle.setCheckable(True);compact_row.addWidget(self.library_toggle)
        compact.addLayout(compact_row);outer.addWidget(self.compact_library)
        self.profile_picker.currentIndexChanged.connect(self.choose_compact_profile)
        row=QHBoxLayout(); row.addWidget(button("＋ 新建",self.new_profile)); row.addWidget(button("复制",self.duplicate_profile)); lv.addLayout(row)
        row=QHBoxLayout(); row.addWidget(button("导入",self.import_profile)); row.addWidget(button("导出",self.export_profile)); lv.addLayout(row)
        lv.addWidget(button("归档当前方案",self.archive_profile));
        self.recover_draft_button=button("恢复历史草稿",self.recover_legacy_draft);lv.addWidget(self.recover_draft_button)
        lv.addWidget(label("JSON 方案 · 本地保存\n归档文件可从目录恢复","subtitle")); lv.addWidget(self.profile_list,1); body.addWidget(library)
        right=QVBoxLayout(); right.setSpacing(13)
        editor,ev=card(); editor.setMinimumHeight(360); row=QHBoxLayout(); self.name=QLineEdit(); self.name.setPlaceholderText("方案名称"); self.name.textChanged.connect(self.edited); row.addWidget(self.name,1)
        self.save_state=label("已保存","subtitle"); row.addWidget(self.save_state); self.save_button=button("保存方案",self.save_profile,'primary');row.addWidget(self.save_button); ev.addLayout(row)
        self.basic_fields=FieldGrid(3,175)
        self.mode=QComboBox(); self.mode.addItem("单幅度 Ramp","ramp"); self.mode.addItem("自定义函数","function")
        self.channel=QComboBox(); self.channel.addItem("DAC 1",0); self.channel.addItem("DAC 2",1); self.channel.hide(); self.channel_badge=label("DAC 1","badge")
        self.repeat=QSpinBox(); self.repeat.setRange(1,65535);self.repeat.setSuffix(' 次')
        self.repeat_mode=fit_combo(QComboBox());self.repeat_mode.addItem('指定次数','finite');self.repeat_mode.addItem('无限循环 · ∞','infinite')
        self.repeat_box=QWidget();repeat_layout=QHBoxLayout(self.repeat_box);repeat_layout.setContentsMargins(0,0,0,0);repeat_layout.setSpacing(8)
        repeat_layout.addWidget(self.repeat_mode,1);repeat_layout.addWidget(self.repeat,1)
        self.repeat_mode.currentIndexChanged.connect(self.edited)
        self.trigger=QComboBox(); self.trigger.addItem("外部触发","external"); self.trigger.addItem("软件触发","software")
        for text,w in [("波形类型",self.mode),("重复次数",self.repeat),("触发源",self.trigger)]:
            self.basic_fields.add_field(text,self.repeat_box if w is self.repeat else w)
            if hasattr(w,"valueChanged"): w.valueChanged.connect(self.edited)
            else: w.currentIndexChanged.connect(self.edited)
        ev.addWidget(self.basic_fields)
        self.playback_fields=FieldGrid(3,175)
        self.design_bits=QComboBox()
        for bits in (10,12,14):self.design_bits.addItem(f'{bits} bit',bits)
        self.playback=QComboBox();self.playback.addItem('逐码保持 · 原有模式','hold');self.playback.addItem('分段线性插值','linear')
        self.interpolation_error=QSpinBox();self.interpolation_error.setRange(0,16383);self.interpolation_error.setValue(1);self.interpolation_error.setSuffix(' 原码')
        self.interpolation_error.setToolTip('与原完整采样取整结果比较，逐点差异不得超过此值；容量不足会报错，不自动放宽。')
        self.design_bits.setToolTip('方案位数独立保存。修改只改变位数设置，不按比例缩放原码；下载时必须与设备一致。')
        for text,w in [('方案 DAC 位数',self.design_bits),('播放编码',self.playback),('最大允许差异',self.interpolation_error)]:
            self.playback_fields.add_field(text,w)
            (w.valueChanged if hasattr(w,'valueChanged') else w.currentIndexChanged).connect(self.edited)
        ev.addWidget(self.playback_fields)
        self.initial=QSpinBox(); self.initial.setRange(0,16383)
        self.amplitude=QSpinBox(); self.amplitude.setRange(0,16383)
        self.safe=QSpinBox(); self.safe.setRange(0,16383)
        self.trigger_input=QComboBox(); self.trigger_input.addItem('输入 1',0); self.trigger_input.addItem('输入 2',1)
        self.trigger_edge=QComboBox(); self.trigger_edge.addItem('上升沿','rising'); self.trigger_edge.addItem('下降沿','falling')
        self.codes_grid=FieldGrid(3,175)
        self.initial_caption=label('初始 / 结束码')
        for text,w in [(self.initial_caption,self.initial),('目标幅度码',self.amplitude),('中止码',self.safe)]:
            self.codes_grid.add_field(text,w)
            if hasattr(w,'valueChanged'): w.valueChanged.connect(self.edited)
            else: w.currentIndexChanged.connect(self.edited)
        ev.addWidget(self.codes_grid)
        self.trigger_fields=FieldGrid(2,175)
        self.trigger_fields.add_field('触发输入',self.trigger_input);self.trigger_fields.add_field('触发边沿',self.trigger_edge)
        ev.addWidget(self.trigger_fields)
        self.trigger_input.currentIndexChanged.connect(self.edited);self.trigger_edge.currentIndexChanged.connect(self.edited)
        self.route_label=label("","subtitle"); self.route_label.hide()
        self.timing_mode=QComboBox(); self.timing_mode.addItem('固定时长 · 触发后完整播放','fixed'); self.timing_mode.addItem('电平门控 · 上升沿进入 / 高电平保持 / 下降沿返回','gate')
        ev.removeWidget(self.codes_grid);ev.removeWidget(self.trigger_fields)
        ev.addWidget(self.timing_mode);ev.addWidget(self.trigger_fields);ev.addWidget(self.codes_grid);self.timing_mode.currentIndexChanged.connect(self.edited)
        self.shape_panel=QWidget(); shape=QVBoxLayout(self.shape_panel); shape.setContentsMargins(0,0,0,0)
        self.ramp_panel=FieldGrid(3,205)
        self.rise_ms=self.time_editor('rise'); self.hold_ms=self.time_editor('hold'); self.fall_ms=self.time_editor('fall')
        for text,w in [("渐变至目标",self.rise_ms),("保持",self.hold_ms),("返回初始值",self.fall_ms)]:
            field=self.ramp_panel.add_field(text,w);w.valueChanged.connect(self.edited)
            if w is self.hold_ms:
                self.gate_hold_label=label('由外部高电平决定','subtitle'); self.gate_hold_label.setWordWrap(True); field.layout().addWidget(self.gate_hold_label)
        shape.addWidget(self.ramp_panel)
        self.function_panel=QWidget(); fn=QVBoxLayout(self.function_panel); fn.setContentsMargins(0,0,0,0)
        row=QHBoxLayout(); row.addWidget(label('函数结构','subtitle'))
        self.function_layout=QComboBox(); self.function_layout.addItem('三段函数 · 进入 / 保持 / 返回','staged'); self.function_layout.addItem('旧版整段函数 · 保留原波形','single')
        row.addWidget(self.function_layout,1); fn.addLayout(row); self.function_layout.currentIndexChanged.connect(self.edited)
        self.staged_function_panel=QWidget(); stages=QVBoxLayout(self.staged_function_panel); stages.setContentsMargins(0,0,0,0)
        self.rise_function=QLineEdit(); self.fall_function=QLineEdit()
        for title,field in [('01  进入段 f(t) =',self.rise_function),('03  返回段 f(t) =',self.fall_function)]:
            row=QHBoxLayout(); row.addWidget(label(title,'subtitle')); row.addWidget(field,1); stages.addLayout(row)
            field.textChanged.connect(self.edited)
            if field is self.rise_function: stages.addWidget(label('02  保持段：恒定输出目标幅度码 A','subtitle'))
        fn.addWidget(self.staged_function_panel)
        self.single_function_panel=QWidget(); single=QVBoxLayout(self.single_function_panel); single.setContentsMargins(0,0,0,0)
        row=QHBoxLayout(); row.addWidget(label("f(t) =","section")); self.function=QLineEdit(); self.function.setPlaceholderText("I - 400 * sin(2*pi*t/T)"); row.addWidget(self.function,1)
        self.function_ms=self.time_editor('whole_function',minimum=.000005)
        row.addWidget(self.function_ms); single.addLayout(row); fn.addWidget(self.single_function_panel)
        self.function.textChanged.connect(self.edited); self.function_ms.valueChanged.connect(self.edited)
        self.function_help=label("","subtitle"); self.function_help.setWordWrap(True); fn.addWidget(self.function_help)
        examples=QHBoxLayout()
        for text in ('高斯','余弦平滑','线性'):
            examples.addWidget(button(text,lambda checked=False,k=text:self.apply_function_template(k)))
        examples.addStretch(); fn.addLayout(examples); shape.addWidget(self.function_panel)
        self.shape_note=label("","subtitle"); self.shape_note.setWordWrap(True); shape.addWidget(self.shape_note)
        ev.addWidget(self.shape_panel)
        self.legacy_editor=QWidget(); legacy=QVBoxLayout(self.legacy_editor); legacy.setContentsMargins(0,0,0,0)
        params=QHBoxLayout()
        self.update_us=self.time_editor('legacy_interval',base_unit='µs',minimum=.08,maximum=327.675)
        for text,w in [("更新间隔",self.update_us)]:
            col=QVBoxLayout(); col.addWidget(label(text,"subtitle")); col.addWidget(w); params.addLayout(col)
            if hasattr(w,"valueChanged"): w.valueChanged.connect(self.edited)
            else: w.currentIndexChanged.connect(self.edited)
        legacy.addLayout(params)
        self.table=QTableWidget(0,4); self.table.setHorizontalHeaderLabels(["阶段","目标码","持续时间 / ms","行为"])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch); self.table.verticalHeader().hide()
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows); self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setMinimumHeight(130); self.table.setMaximumHeight(220); self.table.itemChanged.connect(self.edited); legacy.addWidget(self.table)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.update_us.setEnabled(False)
        legacy_note=label('历史分段方案仅供查看和导出；当前输出使用单幅度 Ramp、自定义函数或执行序列。','notice');legacy_note.setWordWrap(True);legacy.insertWidget(0,legacy_note)
        self.legacy_conversion_note=label('','subtitle');self.legacy_conversion_note.setWordWrap(True);legacy.addWidget(self.legacy_conversion_note)
        row=QHBoxLayout();self.legacy_convert=button('转换为 Ramp 副本',self.convert_legacy_profile,'primary');row.addWidget(self.legacy_convert)
        row.addWidget(button('新建波形方案',self.new_profile));row.addStretch();legacy.addLayout(row)
        ev.addWidget(self.legacy_editor)
        row=QHBoxLayout(); self.compile_button=button("完整离散检查",self.compile_current); row.addWidget(self.compile_button)
        self.duration_label=label("","subtitle"); row.addWidget(self.duration_label); row.addStretch(); ev.addLayout(row)
        self.validation=label(""); self.validation.setWordWrap(True); ev.addWidget(self.validation)
        self.resource_view=ResourceUsageView();ev.addWidget(self.resource_view);right.addWidget(editor)
        graph,gv=card(); row=QHBoxLayout(); row.addWidget(label("波形预览","section")); row.addStretch(); self.preview_note=label("预览抽样显示 · 下载时完整按 5 ns 离散","subtitle");self.preview_note.setWordWrap(True);row.addWidget(self.preview_note); gv.addLayout(row)
        self.plot=WaveformPlot(); gv.addWidget(self.plot,1); right.addWidget(graph,1)
        runtime,rv=card(); self.designer_runtime=runtime; runtime.hide(); row=QHBoxLayout()
        self.run_state=label("待连接","metric"); row.addWidget(self.run_state)
        self.output_label=label("输出 —  /  段 —  /  完成 —","subtitle"); row.addWidget(self.output_label); row.addStretch()
        self.download_label=label("未下载","subtitle"); row.addWidget(self.download_label); rv.addLayout(row)
        self.upload_button=button("1  下载方案",self.upload,"primary")
        self.arm_button=button("2  布防",lambda:self.command(ARM))
        self.trigger_button=button("软件触发",lambda:self.command(TRIGGER))
        self.external_button=button("模拟所选触发",lambda:self.send("external",(self.trigger_input.currentData(),self.trigger_edge.currentData())))
        self.falling_button=button('模拟下降沿',lambda:self.send('external',(self.trigger_input.currentData(),'falling')))
        self.disarm_button=button("取消布防",lambda:self.command(DISARM))
        self.abort_button=button("中止",lambda:self.command(ABORT),"danger")
        self.action_buttons=[self.upload_button,self.arm_button,self.trigger_button,self.external_button,self.falling_button,self.disarm_button,self.abort_button]
        self.action_grid=QGridLayout(); self._action_columns=None; self.arrange_actions()
        rv.addLayout(self.action_grid); right.addWidget(runtime)
        body.addLayout(right,1); outer.addLayout(body,1)
        outer.setSizeConstraint(QLayout.SetMinimumSize)
        scroll=QScrollArea(); scroll.setWidgetResizable(True); scroll.setWidget(page)
        from .sequence_page import SequencePage
        self.designer_tabs=QTabWidget();self.designer_tabs.addTab(scroll,"波形库")
        self.sequence_page=SequencePage(self);self.designer_tabs.addTab(self.sequence_page,"执行序列")
        from .scan_page import ScanPage
        self.scan_page=ScanPage(self);self.designer_tabs.addTab(self.scan_page,'触发步进')
        self.stack.addWidget(self.designer_tabs)

    def time_editor(self,key,**options):
        field=TimeInput(**options)
        unit=self.settings.value('time_units/'+key,field.base_unit)
        try:field.setUnit(unit,preserve_duration=True)
        except ValueError:pass
        field.unitChanged.connect(lambda unit:self.settings.setValue('time_units/'+key,unit))
        return field

    def arrange_actions(self,*args):
        columns=3 if self.width()<1280 else 6
        gate=self.timing_mode.currentData()=='gate'
        hidden=[]
        if gate:hidden.append(self.trigger_button)
        if not self.demo or not self.connected:hidden.extend((self.external_button,self.falling_button))
        elif not gate:hidden.append(self.falling_button)
        visible=[w for w in self.action_buttons if w not in hidden]
        key=(columns,tuple(visible),self.library_toggle.isChecked())
        if key==self._action_columns: return
        self._action_columns=key
        compact=columns==3
        self.compact_library.setVisible(compact)
        self.library_card.setVisible(not compact or self.library_toggle.isChecked())
        self.workspace_body.setDirection(QBoxLayout.TopToBottom if compact else QBoxLayout.LeftToRight)
        self.library_card.setMinimumSize(0,0); self.library_card.setMaximumSize(16777215,16777215)
        if compact: self.library_card.setFixedHeight(280)
        else: self.library_card.setFixedWidth(230)
        for control in self.action_buttons: self.action_grid.removeWidget(control)
        for control in self.action_buttons:control.setVisible(control in visible)
        for i,control in enumerate(visible): self.action_grid.addWidget(control,i//columns,i%columns)
        for i in range(6): self.action_grid.setColumnStretch(i,1 if i<columns else 0)

    def resizeEvent(self,event):
        if hasattr(self,'action_grid'): self.arrange_actions()
        if hasattr(self,'board_page'):self.refresh_dac_navigation()
        super().resizeEvent(event)

    def sync_connection_panel(self,*args):
        expanded=self.board_page.tabs.currentWidget() is self.board_page.info_page
        self.connection_controls.setVisible(expanded)
        self.connection_toggle.setText('收起连接设置' if expanded else '连接设置')

    def toggle_connection_panel(self):
        expanded=self.connection_controls.isHidden()
        self.connection_controls.setVisible(expanded)
        self.connection_toggle.setText('收起连接设置' if expanded else '连接设置')

    def open_debug(self,channel):
        self.debug_page.channel.setCurrentIndex(self.debug_page.channel.findData(channel))
        self.debug_tabs.setCurrentIndex(0)
        self.navigate(3)

    def refresh_hardware_options(self,*args,channel=None,trigger_input=None,edit=True):
        if not hasattr(self,'board_page'): return
        if not self.connected:
            count=self.board_page.dac_count.value()
            self.caps=replace(Capabilities(trigger_scan=True),core_hz=self.board_page.output_rate.currentData(),dac_count=count,trigger_count=self.board_page.trigger_count.value(),dac_bits=self.board_page.dac_bits.currentData(),max_runs=capacity(count))
        for combo,count,caption,selected in [(self.channel,self.caps.dac_count,'DAC',channel),(self.trigger_input,self.caps.trigger_count,'输入',trigger_input)]:
            value=combo.currentData() if selected is None else selected
            if value is None: value=0
            combo.blockSignals(True); combo.clear()
            for i in range(count): combo.addItem(f'{caption} {i+1}',i)
            if value>=count: combo.addItem(f'{caption} {value+1} · 当前不可用',value)
            combo.setCurrentIndex(combo.findData(value)); combo.blockSignals(False)
        limit=(1 << self.caps.dac_bits)-1
        self.function_help.setText(f'直接返回 {self.caps.dac_bits} 位 DAC 原码 0～{limit}；I 为初始码；t、T 单位为秒。支持 sin/cos/exp/where/clip；^ 请写为 **。')
        for field in (self.initial,self.safe,self.amplitude):
            field.setMaximum(max(limit,field.value()))
            field.setToolTip(f'当前 {self.caps.dac_bits} 位范围：0～{limit}；越界原码保留并报错，不自动缩放')
        speed=(' · 演示设备，无物理串口' if self.demo else ' · FT232H USB FIFO' if str(self.ports.currentData()).startswith('usb:') else f' · UART {self.baudrate.currentData():,} baud · 8N1') if self.connected else ''
        self.hardware_summary.setText(f'{self.caps.core_hz/1e6:g} MHz 核心 / {self.caps.core_hz/self.caps.dac_divider/1e6:g} MHz DAC / {self.caps.dac_bits} bit / {self.caps.dac_count} 路 DAC / {self.caps.trigger_count} 路触发'+speed)
        if self.connected and not self.demo and not str(self.ports.currentData()).startswith('usb:'):
            self.hardware_summary.setText(self.hardware_summary.text()+(' · 波特率自动适配' if self.caps.uart_autobaud else ' · 旧固件固定波特率'))
        desired=self.board_page.output_rate.currentData()
        if self.connected and desired!=self.caps.core_hz:
            self.hardware_summary.setText(self.hardware_summary.text()+f' · 板卡配置 {desired/1e6:g} MSPS 尚未生效，需重新烧录')
        if self.connected and self.board_page.dac_model.currentData()=='3PD5651E' and self.caps.core_hz>125_000_000:
            self.hardware_summary.setText(self.hardware_summary.text()+' · 超过 3PD5651E 上限，请先烧录降频固件')
        if hasattr(self,"sequence_page"):self.sequence_page.refresh_context()
        if hasattr(self,'scan_page'):self.scan_page.refresh_context()
        if edit: self.edited()

    def resource_caps(self):
        if self.connected:return self.caps
        return replace(self.caps,max_runs=capacity(self.caps.dac_count),sequence_steps=64,infinite_repeat=True,
            trigger_scan=True,interpolation=True,interpolation_min_ticks=32)

    def designer_caps(self):
        # A saved design can target newer firmware. Device preflight still uses
        # the capability read from the board before any download writes.
        return replace(self.caps,infinite_repeat=True,interpolation=True,dac_bits=self.design_bits.currentData())

    def resource_source(self):
        return '演示设备' if self.connected and self.demo else '设备回报' if self.connected else '离线预估 · 连接后确认'

    def resource_usage(self,slot,profile):
        return self.resource_monitor.request(slot,profile,self.resource_caps())

    def refresh_resources(self):
        if not hasattr(self,'board_page'):return
        caps=self.resource_caps();source=self.resource_source()
        for slot in list(self.resource_monitor.wanted):
            if slot.startswith('channel:') and int(slot.split(':')[1])>=caps.dac_count:
                self.resource_monitor.request(slot,None,caps)
        self.capacity_summary.setText(f'{source} · 每路 {caps.max_runs:,} 个 BRAM 存储槽 · 事件步骤 {caps.sequence_steps} / 路 · '+('支持分段插值' if caps.interpolation else '不支持分段插值'))
        self.capacity_refresh.setEnabled(self.connected and not self.busy and not self.board_page.flash_page.running())
        p=self.profile if self.profile_valid else None
        report=self.resource_usage('designer',p)
        self.resource_view.set_report(report,source,p is not None)
        if report and not report.fits:self.upload_button.setEnabled(False)
        if p is None:self.resource_view.label.setText(source+' · 参数待修正，资源检查暂停')
        for name in ('sequence_page','scan_page'):
            page=getattr(self,name,None)
            if page:
                p=page.collect() if page.valid else None
                page.resource_view.set_report(self.resource_usage(name,p),source,p is not None)
                if p is None:page.resource_view.label.setText(source+' · 参数待修正，资源检查暂停')
        if hasattr(self,'parallel_page'):self.parallel_page.refresh()
        if hasattr(self,'channel_page'):self.channel_page.refresh()

    def on_capabilities(self,caps):
        # The connect result establishes the connection first; subsequent INFO
        # responses may change limits without a board-template edit.
        if self.connected and caps!=self.caps:
            self.caps=caps;self.refresh_hardware_options()
        self.refresh_resources()

    def on_resource_reports(self,reports):
        for report in reports:self.resource_monitor.remember(report)
        self.library.log('resource_check',demo=self.demo,reports=[r.report() for r in reports])
        self.refresh_resources()

    def make_history(self):
        page=QWidget(); v=QVBoxLayout(page); v.addWidget(label("运行记录","title"))
        v.addWidget(label("记录方案快照、设备模式、下载 ID、操作结果与完成事件。","subtitle"))
        row=QHBoxLayout(); row.addWidget(button("刷新日志",self.refresh_history)); row.addWidget(button("打开数据目录",lambda:QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.library.root))))); row.addStretch(); v.addLayout(row)
        self.history=QPlainTextEdit(); self.history.setReadOnly(True); self.history.setMaximumBlockCount(2000); v.addWidget(self.history); self.stack.addWidget(page)

    def navigate(self,index):
        self.stack.setCurrentIndex(index)
        if index==5:self.channel_page.refresh_library()
        self.refresh_dac_navigation()
        if index==2: self.refresh_history()
        if index==4:self.parallel_page.refresh_library()

    def refresh_history(self):
        folder=self.library.root/"logs"
        files=sorted(folder.glob("*.jsonl")) if folder.exists() else []
        self.history.setPlainText("\n".join(f.read_text(encoding="utf-8") for f in files[-3:])[-100000:] or "暂无记录。连接演示设备即可体验完整流程。")

    def refresh_ports(self):
        if self.connected: return
        selected=self.ports.currentData()
        self.ports.clear(); self.ports.addItem("演示设备 · 无需硬件","demo")
        try:
            from serial.tools import list_ports
            for p in list_ports.comports(): self.ports.addItem(f"{p.device} · {p.description}",p.device)
        except Exception as exc: self.notice(str(exc))
        try:
            from .usb_transport import enumerate_usb_devices
            for device in enumerate_usb_devices():
                self.ports.addItem(f"USB FIFO · {device['serial']} · {device['description']}",device['port'])
        except (OSError,RuntimeError,ImportError) as exc:
            # UART/demo must remain usable without an installed FTDI driver.
            self.ports.setToolTip(f'高速 USB 暂不可枚举：{exc}\n串口和演示连接仍可使用。')
        if selected is not None:
            if self.ports.findData(selected)<0:self.ports.addItem(f'{selected} · 当前未找到',selected)
            self.ports.setCurrentIndex(self.ports.findData(selected))

    def sync_transport_controls(self,*args):
        serial=not str(self.ports.currentData()).startswith('usb:')
        self.baudrate.setVisible(serial);self.baudrate_label.setVisible(serial)

    def refresh_library(self):
        from .legacy_profile import recoverable_drafts
        self.recover_draft_button.setVisible(bool(recoverable_drafts(self.library.root)))
        self.profile_list.blockSignals(True); self.profile_list.clear()
        for p in sorted(self.library.list(),key=lambda p:(p.mode=="segments",p.name)):
            if p.mode in ('sequence','scan'):continue
            item=QListWidgetItem(p.name+(" · 历史只读" if p.mode=="segments" else "")); item.setData(Qt.UserRole,p); self.profile_list.addItem(item)
            if p.id==self.profile.id: self.profile_list.setCurrentItem(item)
        self.profile_list.blockSignals(False); self.filter_library()
        self.refresh_profile_picker()
        if hasattr(self,"sequence_page"):self.sequence_page.refresh_library()
        if hasattr(self,'scan_page'):self.scan_page.refresh_library()
        if hasattr(self,"channel_page"):
            self.parallel_page.refresh_library(); self.channel_page.refresh_library()

    def filter_library(self):
        term=self.search.text().casefold()
        for i in range(self.profile_list.count()): self.profile_list.item(i).setHidden(term not in self.profile_list.item(i).text().casefold())

    def refresh_profile_picker(self):
        self.profile_picker.blockSignals(True);self.profile_picker.clear()
        self.profile_picker.addItem('新建 / 未保存',None)
        for i in range(self.profile_list.count()):
            p=self.profile_list.item(i).data(Qt.UserRole);self.profile_picker.addItem(p.name+(" · 历史只读" if p.mode=="segments" else ""),p.id)
        self.profile_picker.setCurrentIndex(max(0,self.profile_picker.findData(self.profile.id)))
        self.profile_picker.blockSignals(False)

    def choose_compact_profile(self):
        key=self.profile_picker.currentData()
        item=next((self.profile_list.item(i) for i in range(self.profile_list.count()) if self.profile_list.item(i).data(Qt.UserRole).id==key),None)
        if item:self.choose_profile(item,self.profile_list.currentItem())
        self.refresh_profile_picker()

    def maybe_discard(self):
        if self.legacy_readonly:return True
        if self.saved_fingerprint==fingerprint(self.profile) and self.profile_valid: return True
        result=QMessageBox.question(self,"未保存的修改","当前方案有未保存修改。保存后继续？",QMessageBox.Save|QMessageBox.Discard|QMessageBox.Cancel,QMessageBox.Save)
        if result==QMessageBox.Cancel: return False
        if result==QMessageBox.Save: return self.save_profile()
        return True

    def choose_profile(self,item,previous):
        if not item or self.loading: return
        if item.data(Qt.UserRole).id==self.profile.id: return
        if self.maybe_discard():
            self.load_profile(copy.deepcopy(item.data(Qt.UserRole)))
        elif previous:
            self.profile_list.blockSignals(True); self.profile_list.setCurrentItem(previous); self.profile_list.blockSignals(False)

    def load_profile(self,p):
        if p.mode=='scan':
            self.scan_page.load(p);self.designer_tabs.setCurrentIndex(2);return
        if p.mode=="sequence":
            self.sequence_page.load(p);self.designer_tabs.setCurrentIndex(1);return
        p=replace(p,channel=0)
        self.loading=True; self.profile=copy.deepcopy(p);self.legacy_readonly=p.mode=='segments'
        old_index=self.mode.findData('segments')
        if self.legacy_readonly and old_index<0:self.mode.addItem('历史分段 · 只读','segments')
        elif not self.legacy_readonly and old_index>=0:self.mode.removeItem(old_index)
        self.refresh_hardware_options(channel=p.channel,trigger_input=p.trigger_input,edit=False)
        for field,value in [(self.initial,p.initial),(self.safe,p.safe),(self.amplitude,p.amplitude)]: field.setRange(0,max((1 << self.caps.dac_bits)-1,value))
        self.name.setText(p.name); self.initial.setValue(p.initial); self.safe.setValue(p.safe); self.update_us.setValue(p.update_us); self.repeat.setValue(p.repeat or 1)
        self.design_bits.setCurrentIndex(self.design_bits.findData(p.dac_bits))
        self.playback.setCurrentIndex(self.playback.findData(p.playback));self.interpolation_error.setValue(p.interpolation_error)
        self.repeat_mode.setCurrentIndex(self.repeat_mode.findData('infinite' if p.repeat==0 else 'finite'))
        self.trigger.setCurrentIndex(0 if p.trigger=="external" else 1); self.fill_table(p.segments)
        self.mode.setCurrentIndex(self.mode.findData(p.mode)); self.channel.setCurrentIndex(self.channel.findData(p.channel))
        self.timing_mode.setCurrentIndex(self.timing_mode.findData(p.timing_mode))
        self.trigger_input.setCurrentIndex(self.trigger_input.findData(p.trigger_input)); self.trigger_edge.setCurrentIndex(self.trigger_edge.findData(p.trigger_edge))
        self.amplitude.setValue(p.amplitude); self.rise_ms.setValue(p.rise_ms); self.hold_ms.setValue(p.hold_ms); self.fall_ms.setValue(p.fall_ms)
        self.function.setText(p.function); self.function_ms.setValue(p.function_ms)
        self.function_layout.setCurrentIndex(self.function_layout.findData(p.function_layout))
        self.rise_function.setText(p.rise_function); self.fall_function.setText(p.fall_function)
        self.loading=False
        self.saved_fingerprint=fingerprint(p) if (self.library.profiles/f"{p.id}.json").exists() else ""
        self.profile_list.blockSignals(True)
        for i in range(self.profile_list.count()):
            if self.profile_list.item(i).data(Qt.UserRole).id==p.id: self.profile_list.setCurrentRow(i)
        self.profile_list.blockSignals(False)
        self.refresh_profile_picker()
        self.edited()

    def fill_table(self,segments):
        self.table.blockSignals(True); self.table.setRowCount(len(segments))
        previous=self.initial.value()
        for i,s in enumerate(segments):
            for j,text in enumerate([f"{i+1:02d}",str(s.target),f"{s.duration_ms:g}","保持" if s.target==previous else "上升" if s.target>previous else "下降"]):
                item=QTableWidgetItem(text)
                if j in (0,3): item.setFlags(item.flags() & ~Qt.ItemIsEditable)
                self.table.setItem(i,j,item)
            previous=s.target
        self.table.blockSignals(False)

    def collect(self):
        if self.legacy_readonly:return copy.deepcopy(self.profile)
        p=copy.deepcopy(self.profile)
        p.name=self.name.text(); p.initial=self.initial.value(); p.safe=self.safe.value(); p.update_us=self.update_us.value(); p.repeat=self.repeat.value(); p.trigger=self.trigger.currentData()
        try: p.segments=[Segment(int(self.table.item(i,1).text()),float(self.table.item(i,2).text())) for i in range(self.table.rowCount())]
        except (ValueError,AttributeError): raise ValueError("段目标必须为整数，时长必须为数字")
        p.mode=self.mode.currentData(); p.channel=self.channel.currentData()
        p.timing_mode=self.timing_mode.currentData()
        p.repeat=0 if p.timing_mode!='gate' and self.repeat_mode.currentData()=='infinite' else self.repeat.value()
        p.amplitude=self.amplitude.value(); p.rise_ms=self.rise_ms.value(); p.hold_ms=self.hold_ms.value(); p.fall_ms=self.fall_ms.value()
        p.function=self.function.text(); p.function_ms=self.function_ms.value()
        p.function_layout=self.function_layout.currentData(); p.rise_function=self.rise_function.text(); p.fall_function=self.fall_function.text()
        p.playback=self.playback.currentData();p.interpolation_error=self.interpolation_error.value()
        p.trigger_input=self.trigger_input.currentData(); p.trigger_edge=self.trigger_edge.currentData()
        p.dac_bits=self.design_bits.currentData()
        compile_profile(p,self.designer_caps()); return p

    def edited(self,*args):
        if self.loading: return
        if self.legacy_readonly:
            self.show_legacy_profile();return
        self.name.setReadOnly(False);self.basic_fields.show();self.codes_grid.setEnabled(True);self.timing_mode.show()
        self.initial_caption.setText('初始 / 结束码');self.preview_note.setText(f'预览抽样显示 · 下载时按 {1e9/self.caps.core_hz:g} ns 离散')
        mode=self.mode.currentData()
        linear=self.playback.currentData()=='linear'
        self.playback_fields.setVisible(mode!='segments')
        self.playback_fields.show_field(self.interpolation_error,linear)
        if mode=='function' and self.profile.mode!='function':
            self.function_layout.blockSignals(True); self.function_layout.setCurrentIndex(0); self.function_layout.blockSignals(False)
        staged=mode=='function' and self.function_layout.currentData()=='staged'
        gate=self.timing_mode.currentData()=='gate'
        self.trigger_button.setVisible(not gate)
        if gate:
            for combo in (self.trigger,self.trigger_edge):
                combo.blockSignals(True); combo.setCurrentIndex(0); combo.blockSignals(False)
        infinite=not gate and self.repeat_mode.currentData()=='infinite'
        self.trigger.setEnabled(not gate); self.repeat.setEnabled(not gate);self.repeat_mode.setEnabled(not gate)
        self.repeat.setVisible(not infinite)
        self.repeat_mode.setToolTip('一次触发后持续循环，直到在 DAC 页面点击“中止本通道”；循环期间无需重复触发。')
        self.basic_fields.show_field(self.repeat_box,not gate);self.basic_fields.show_field(self.trigger,not gate)
        self.codes_grid.show_field(self.amplitude,mode=='ramp' or staged)
        self.hold_ms.setVisible(not gate); self.gate_hold_label.setVisible(gate)
        self.shape_panel.setVisible(mode!='segments'); self.legacy_editor.setVisible(mode=='segments')
        self.ramp_panel.setVisible(mode=='ramp' or staged); self.function_panel.setVisible(mode=='function')
        self.staged_function_panel.setVisible(staged); self.single_function_panel.setVisible(not staged)
        limit=(1<<self.design_bits.currentData())-1
        for field in (self.initial,self.safe,self.amplitude):field.setMaximum(max(limit,field.value()))
        self.function_help.setText((f'返回 DAC 原码 0～{limit}；I = 初始码，A = 目标码。t 从每段 0 重新计时，T 为该段量化后时长（秒）。保持段自动输出 A。' if staged else f'旧版整段表达式：返回 DAC 原码 0～{limit}；I 为初始码；t、T 为整段时间（秒）。')+' 支持 sin/cos/exp/where/clip；乘方使用 **。')
        self.channel.setEnabled(mode!='segments'); self.compile_button.setVisible(mode!='segments')
        self.shape_note.setText((f'{self.initial.value()} → {self.amplitude.value()} → 保持 → {self.initial.value()}；' if mode=='ramp' or staged else f'函数结束返回 {self.initial.value()}；')+f'中止返回 {self.safe.value()}。保存后可供多个 DAC 选择；原码不自动缩放。')
        external=self.trigger.currentData()=='external' and mode!='segments'
        self.trigger_fields.setVisible(external);self.trigger_fields.show_field(self.trigger_edge,not gate)
        self.trigger_input.setEnabled(external); self.trigger_edge.setEnabled(external and not gate)
        if gate:self.shape_note.setText('上升沿进入 → 高电平保持目标 → 下降沿返回；每次自动重新布防。提前下降沿从当前码线性返回（准备需位数 + 1 个时钟）。中止立即输出中止码。')
        elif infinite:
            self.shape_note.setText(f'一次触发后持续循环；点击“中止本通道”停止并输出 {self.safe.value()}。无需每轮触发，关闭上位机不会停止 FPGA。')
            self.preview_note.setText('仅预览单轮 · 下载单轮数据后由 FPGA 循环播放')
        index=self.channel.currentData() if mode!='segments' else 0
        source=self.trigger_input.currentData() if mode!='segments' else 0
        pin='待分配'
        if hasattr(self,'board_page'):
            for row in range(self.board_page.table.rowCount()):
                if self.board_page.table.item(row,0).text()==f'trigger_in[{source}]':
                    pin=self.board_page.table.item(row,1).text() or '待分配'
        route=f'输入 {source+1} · trigger_in[{source}] · {self.trigger_edge.currentText()} · 配置管脚 {pin}' if self.trigger.currentData()=='external' else '软件触发 · 无需外部触发线'
        if gate:route=f'输入 {source+1} · trigger_in[{source}] · 上升进入 / 下降返回 · 配置管脚 {pin}'
        self.route_label.setText(f'DAC {index+1}：dac_data[{index*self.caps.dac_bits+self.caps.dac_bits-1}:{index*self.caps.dac_bits}] / dac_clk[{index}] ← {route}。硬件变更须重新构建；接线说明见硬件子页面。')
        try:
            p=self.collect(); c=compile_profile(p,self.designer_caps()); self.profile=p; self.profile_valid=True
            self.plot.caps=self.designer_caps(); self.plot.set_profile(p); self.validation.setText(f"实际更新间隔 {c.divider/self.caps.core_hz*1e6:g} µs · {len(c.counts)} 段 · 时间量化偏差 {c.duration_ms-c.requested_ms:+.6g} ms")
            if p.mode!='segments':
                from .functions import preview_samples
                preview_samples(p,self.caps.core_hz)
                if linear:
                    self.validation.setText(f'{self.caps.core_hz/1e6:g} MSPS · {1e9/self.caps.core_hz:g} ns/点 · 最大允许差异 {p.interpolation_error} 原码 · 完整逐点检查后确认插值段数；每段至少 32 拍 / {32e9/self.caps.core_hz:g} ns')
                    self.preview_note.setText('原函数抽样参考 · 插值误差以完整检查为准')
                else:self.validation.setText(f"{self.caps.core_hz/1e6:g} MSPS · {1e9/self.caps.core_hz:g} ns/点 · {round(c.duration_ms*self.caps.core_hz/1000):,} 点 · 原码四舍五入（误差 ≤ 0.5 LSB）· 请用完整离散检查验证容量与全部采样点")
            self.validation.setStyleSheet("color:#647c9e;font-size:11px;")
            shot,shot_unit=display_time(c.duration_ms); total,total_unit=display_time(c.duration_ms*p.repeat)
            self.duration_label.setText(f"单次 {shot:g} {shot_unit} · 总计 {total:g} {total_unit}")
            if infinite:
                self.duration_label.setText(f'单轮 {shot:g} {shot_unit} · ∞ 无限循环，直到中止')
                if self.connected and not self.caps.infinite_repeat:
                    self.duration_label.setText(self.duration_label.text()+' · 当前固件不支持，可保存方案，下载前需升级')
            if gate:self.duration_label.setText(f'进入与返回共 {shot:g} {shot_unit} · 保持由输入决定 · 持续布防')
            self.save_state.setText("已保存" if fingerprint(p)==self.saved_fingerprint else "未保存修改")
            previous=p.initial
            self.table.blockSignals(True)
            for i,s in enumerate(p.segments):
                if self.table.item(i,3): self.table.item(i,3).setText("保持" if s.target==previous else "上升" if s.target>previous else "下降")
                previous=s.target
            self.table.blockSignals(False)
        except ValueError as exc:
            self.profile_valid=False; self.validation.setText(str(exc)); self.validation.setStyleSheet("color:#bd6650;"); self.save_state.setText("参数待修正"); self.plot.set_profile(None)
        self.update_buttons()

    def show_legacy_profile(self):
        from .legacy_profile import ramp_copy
        p=self.profile
        self.playback_fields.hide()
        self.initial_caption.setText('初始码');self.preview_note.setText(f'历史波形预览 · 原更新间隔 {p.update_us:g} µs')
        self.name.setReadOnly(True);self.basic_fields.hide();self.codes_grid.setEnabled(False);self.codes_grid.show_field(self.amplitude,False)
        self.timing_mode.hide();self.trigger_fields.hide();self.shape_panel.hide();self.legacy_editor.show();self.compile_button.hide()
        self.profile_valid=False;self.save_state.setText('历史方案 · 只读')
        self.plot.caps=Capabilities(dac_bits=p.dac_bits);self.plot.set_profile(p)
        self.validation.setText('原文件保持不变。转换只生成新副本，不会自动保存、下载或改变任何 DAC 输出。');self.validation.setStyleSheet('color:#647c9e;font-size:11px;')
        c=compile_profile(p,Capabilities(dac_bits=p.dac_bits));value,unit=display_time(c.duration_ms)
        self.duration_label.setText(f'历史波形总时长 {value:g} {unit}')
        try:
            ramp_copy(p);self.legacy_convert.setEnabled(True)
            self.legacy_conversion_note.setText('可保留初始/目标/中止码与三段时长，转换为单幅度 Ramp；采样将按当前板卡速率重新离散，请检查预览后保存。旧版独立扫描参数不会迁入。')
        except ValueError as exc:self.legacy_convert.setEnabled(False);self.legacy_conversion_note.setText(str(exc))
        self.update_buttons()

    def convert_legacy_profile(self):
        from .legacy_profile import ramp_copy
        try:
            p=ramp_copy(self.profile)
            if p.dac_bits!=self.caps.dac_bits:raise ValueError('请先将板卡位数设为与历史方案一致，再转换；原码不会自动缩放')
            self.load_profile(p);self.notice('已生成 Ramp 副本，请核对原码、三个时长和触发设置后保存。原文件保持不变。')
        except ValueError as exc:self.notice(str(exc))

    def new_profile(self):
        if self.maybe_discard():
            p=Profile(name="新实验方案",mode="ramp",channel=self.channel.currentData(),dac_bits=self.caps.dac_bits,initial=1 << (self.caps.dac_bits-1),safe=1 << (self.caps.dac_bits-1),amplitude=(1 << (self.caps.dac_bits-1))-400); self.library.save(p); self.refresh_library(); self.load_profile(p)

    def apply_function_template(self,kind):
        from .functions import STAGED_TEMPLATES
        self.loading=True
        self.function_layout.setCurrentIndex(self.function_layout.findData('staged'))
        up,down=STAGED_TEMPLATES[kind]
        self.rise_function.setText(up); self.fall_function.setText(down)
        self.loading=False; self.edited()
        self.notice(f'已应用三段{kind}：初始码 → 目标码 → 保持 → 初始码；请确认目标码及三个时长后保存、重新下载')

    def duplicate_profile(self):
        if self.legacy_readonly:self.convert_legacy_profile();return
        try:
            p=self.collect(); p.id=uuid.uuid4().hex; p.name += " · 副本"; self.library.save(p); self.refresh_library(); self.load_profile(p)
        except ValueError as exc: self.notice(str(exc))

    def save_profile(self):
        if self.legacy_readonly:
            self.notice("历史方案只读，请转换为 Ramp 副本或新建波形方案");return False
        try:
            self.profile=self.collect(); self.library.save(self.profile); self.saved_fingerprint=fingerprint(self.profile)
            self.refresh_library(); self.edited(); self.notice("方案已保存"); return True
        except Exception as exc: QMessageBox.warning(self,"无法保存",str(exc)); return False

    def archive_profile(self):
        if not self.maybe_discard(): return
        self.library.archive(self.profile); self.refresh_library()
        profiles=self.library.list(); self.load_profile(profiles[0] if profiles else Profile()); self.notice("方案已移到 archive，可从数据目录恢复")

    def export_profile(self):
        try:
            p=self.collect(); path,_=QFileDialog.getSaveFileName(self,"导出方案",p.name+".json","JSON (*.json)")
            if path: Path(path).write_text(json.dumps(p.to_dict(),ensure_ascii=False,indent=2),encoding="utf-8")
        except Exception as exc: QMessageBox.warning(self,"导出失败",str(exc))

    def import_profile(self):
        if not self.maybe_discard(): return
        path,_=QFileDialog.getOpenFileName(self,"导入方案","","JSON (*.json)")
        if not path: return
        try:
            p=Profile.from_dict(json.loads(Path(path).read_text(encoding="utf-8"))); p.id=uuid.uuid4().hex
            self.library.save(p); self.refresh_library(); self.load_profile(p)
        except Exception as exc: QMessageBox.warning(self,"导入失败",str(exc))

    def send(self,operation,arg):
        self.hardware_model.emit(self.board_page.dac_model.currentData())
        self.busy=True; self.update_buttons(); self.request.emit(operation,arg)

    def toggle_connect(self):
        if self.connected: self.send("disconnect",None)
        else:
            self.demo=self.ports.currentData()=="demo"
            target=dict(port='demo',core_hz=self.board_page.output_rate.currentData(),dac_count=self.board_page.dac_count.value(),trigger_count=self.board_page.trigger_count.value(),dac_bits=self.board_page.dac_bits.currentData()) if self.demo else dict(port=self.ports.currentData(),baudrate=self.baudrate.currentData())
            target['dac_model']=self.board_page.dac_model.currentData()
            self.send("connect",target)

    def compile_current(self):
        if self.legacy_readonly:return
        try:
            p=self.collect(); self.compiling_fingerprint=fingerprint(p); self.send("compile",dict(profile=p,caps=self.resource_caps()))
        except ValueError as exc: self.notice(str(exc))

    def upload(self):
        if self.legacy_readonly:return
        try:
            p=self.collect(); self.uploading_profile=copy.deepcopy(p)
            self.uploaded_fingerprint=""; self.uploaded_id=None; self.download_label.setText("下载中…")
            self.send("upload",p)
        except Exception as exc: self.notice(str(exc))

    def parallel_operation(self,operation,argument):
        if not self.connected or self.busy:return
        if operation=='static':
            self.channel_uploads.pop(argument['channel'],None)
        self.send(operation,argument)

    def command(self,cmd):
        if cmd==ABORT and not self.demo:
            # The action is explicitly labeled and uses the configured safe code.
            self.notice(f"发送中止命令；输出返回已下载方案的中止码")
        channel=self.debug_page.channel.currentData() if self.stack.currentIndex()==3 else self.channel.currentData()
        self.send("command",dict(command=cmd,channel=channel) if self.caps.parallel_channels else cmd)

    def set_static_output(self,parameters):
        if not self.connected or self.busy:
            self.notice('请先连接设备，并等待当前操作结束'); return
        status=self.channel_statuses.get(parameters['channel']) if self.caps.parallel_channels else self.last_status
        if not status or status['state'] not in (0,3):
            self.notice('请先中止或取消布防，再应用静态输出'); return
        self.channel_uploads.pop(parameters['channel'],None)
        self.uploaded_fingerprint=''; self.uploaded_id=None
        self.download_label.setText('静态调试 · 运行波形前请重新下载')
        self.debug_page.pending=True; self.debug_page.failure=None
        self.library.log('static_request',demo=self.demo,**parameters)
        self.send('static',parameters)

    def worker_result(self,operation,result):
        self.busy=False
        self.channel_page.finish(operation)
        self.parallel_page.result(operation,result)
        if operation=='upload_many':
            for p,identity in zip(self.parallel_page.pending_profiles,result):self.channel_uploads[p.channel]=(fingerprint(p),identity)
        if operation=="connect":
            self.parallel_page.store.connect('demo' if self.demo else str(self.ports.currentData()))
            self.connected=True; self.caps=result; self.connection_badge.setText("DEMO · 演示设备" if self.demo else "ONLINE · 硬件设备")
            self.side_status.setText("●  演示模式" if self.demo else "●  已连接")
            self.refresh_hardware_options()
        elif operation=="disconnect": self.set_disconnected()
        elif operation=="upload":
            self.uploaded_fingerprint=fingerprint(self.uploading_profile); self.uploaded_id=result
            self.channel_uploads[self.uploading_profile.channel]=(self.uploaded_fingerprint,result)
            self.parallel_page.identities[self.uploading_profile.channel]=(result,self.uploaded_fingerprint)
            self.download_label.setText(f"已下载 · {result:08X}")
            self.library.log("upload",demo=self.demo,config_id=result,profile=self.uploading_profile.to_dict())
        elif operation=='static':
            self.debug_page.pending=False; self.debug_page.failure=None; self.debug_page.confirmed=result
            self.library.log('static_confirmed',demo=self.demo,**result)
            self.on_status(result['status'])
        elif operation=="compile_sequence":self.sequence_page.result(result)
        elif operation=="compile" and self.profile_valid and self.compiling_fingerprint==fingerprint(self.profile):
            limit=self.resource_caps().max_runs;verdict='容量超限，无法下发' if len(result.runs)>limit else '完整离散通过'
            if getattr(result,'interpolation',False):
                self.validation.setText(f'{self.resource_source()} · {verdict}：插值段 {len(result.runs):,} / {limit:,} · 已检查全部 {result.checked_samples:,} 点 · 实际最大差异 {result.max_error:g} / 允许 {result.error_limit} 原码')
            else:self.validation.setText(f"{self.resource_source()} · {verdict}：{result.total:,} 点 / {len(result.runs):,} 个连续码段（每路容量 {limit:,}）· 最大取整误差 {result.max_error:.4g} LSB")
        if operation!="upload": self.library.log(operation,demo=self.demo,result="完整采样检查通过" if operation=="compile" else str(result))
        self.notice({"connect":"设备已连接","disconnect":"已断开；硬件会继续执行已提交任务","upload":"方案已校验并提交","command":"命令已确认","prepare":"所选通道准备完成","upload_many":"所选波形已下载并确认","group":"所选通道命令已确认","external":"演示外部触发已发送","trigger_debug":"触发调试命令已确认 · 请查看各 DAC 回读状态","compile":"全部采样点已按当前速率检查，未跳点","static":"静态原码已提交并回读确认；运行波形前请重新下载方案"}.get(operation,operation))
        self.update_buttons()
        if operation=='capabilities':self.notice('设备能力已重新读取；容量提示已更新')

    def worker_applied(self,data):
        p=data['profile'];identity=data['config_id']
        try:self.parallel_page.store.confirm(p,identity,self.caps)
        except OSError as exc:self.notice(f'加载已确认；本地波形快照缓存写入失败：{exc}')
        self.channel_uploads[p.channel]=(fingerprint(p),identity)
        self.library.log('waveform_confirmed',demo=self.demo,config_id=identity,profile=p.to_dict())
        self.on_status(data['status'])

    def set_disconnected(self):
        self.connected=False; self.last_status=None; self.uploaded_fingerprint=""; self.uploaded_id=None; self.channel_statuses={}; self.channel_uploads={}
        self.parallel_page.identities={}
        self.refresh_hardware_options()
        self.connection_badge.setText("OFFLINE"); self.side_status.setText("●  未连接"); self.run_state.setText("待连接"); self.download_label.setText("未确认下载")
        self.plot.status=None; self.plot.update()
        self.debug_page.confirmed=None; self.debug_page.pending=False; self.debug_page.failure=None

    def worker_failed(self,operation,message):
        self.busy=False
        if operation=='disconnect':
            self._closing_disconnect=False;self._exit_saved=False;self.centralWidget().setEnabled(True)
        self.channel_page.finish(operation,message)
        if operation=="compile_sequence":self.sequence_page.feedback.setText(message)
        if operation in ("prepare","upload_many","group","static"):self.parallel_page.actions.message("操作未完成："+message+"；请核对各路实际状态。")
        if operation in ("connect","connection_lost"): self.set_disconnected()
        if operation=="upload": self.download_label.setText("下载未确认")
        if operation=='static':
            self.debug_page.pending=False; self.debug_page.failure=message; self.debug_page.confirmed=None
            self.download_label.setText('静态输出未确认 · 运行波形前请重新下载')
        self.library.log("error",operation=operation,message=message,demo=self.demo)
        self.notice(message); self.update_buttons()
        QMessageBox.warning(self,"操作未完成",message)

    def on_status(self,status):
        if not self.connected: return
        if self.caps.parallel_channels:
            if 'channels' in status:
                self.channel_statuses={s['channel']:s for s in status['channels']}
            else:self.channel_statuses[status['channel']]=status
            channel=self.channel.currentData()
            status=self.channel_statuses.get(channel)
            if status is None:return
            saved=self.channel_uploads.get(channel,('',None))
            self.uploaded_fingerprint,self.uploaded_id=saved
        if self.last_status and any(status.get(k)!=self.last_status.get(k) for k in ('state','gate_phase','loops')):
            self.library.log("state_change",demo=self.demo,**status)
        self.last_status=status; self.run_state.setText(STATES.get(status["state"],"未知状态"))
        if status.get('gate_mode') and status['state'] in (1,2):
            self.run_state.setText({0:'等待上升沿',1:'正在进入',2:'高电平保持',3:'正在返回',4:'提前返回 · 计算中',5:'提前返回 · 线性',6:'提前返回 · 准备中'}.get(status.get('gate_phase',0),'门控'))
        self.output_label.setText(f"DAC {status.get('channel',0)+1} · 输出 {status['code']}  /  索引 {status.get('run_index',status['segment'])+1}  /  完成 {status['loops']}")
        if self.uploaded_id is not None and status["config_id"]!=self.uploaded_id:
            if self.caps.parallel_channels:self.channel_uploads.pop(status['channel'],None)
            self.uploaded_fingerprint=""; self.uploaded_id=None; self.download_label.setText("设备配置已变化，请重新下载")
        self.plot.status=None; self.plot.update(); self.update_buttons()

    def update_buttons(self):
        if not hasattr(self,"upload_button"): return
        if hasattr(self,"sequence_page"):
            self.sequence_page.check.setEnabled(not self.busy)
            self.sequence_page.save_button.setEnabled(not self.busy and self.sequence_page.valid)
        if hasattr(self,'scan_page'):self.scan_page.save_button.setEnabled(not self.busy and self.scan_page.valid)
        self.refresh_dac_navigation()
        if self.caps.parallel_channels:
            channel=self.channel.currentData()
            self.last_status=self.channel_statuses.get(channel)
            self.uploaded_fingerprint,self.uploaded_id=self.channel_uploads.get(channel,('',None))
        if hasattr(self,'parallel_page'):
            states=self.channel_statuses if self.caps.parallel_channels else {self.last_status['channel']:self.last_status} if self.last_status else {}
            self.parallel_page.boot_profiles=self.board_page.flash_page.boot_profiles
            self.parallel_page.set_context(self.caps,self.connected,self.busy,states)
        state=self.last_status["state"] if self.last_status else None
        ready=self.connected and not self.busy
        matches=self.profile_valid and self.uploaded_fingerprint==fingerprint(self.profile) and self.uploaded_id is not None
        flash=self.board_page.flash_page;flash.serial_connected=self.connected or self.busy;flash.refresh()
        self.connect_button.setText("断开连接" if self.connected else "连接设备"); self.connect_button.setEnabled(not self.busy and not flash.running()); self.ports.setEnabled(not self.connected and not self.busy and not flash.running())
        self.baudrate.setEnabled(not self.connected and not self.busy and not flash.running())
        save_blocked=self.workspace_transfer_blocked();import_blocked=self.workspace_transfer_blocked(importing=True)
        self.workspace_save_button.setEnabled(not save_blocked)
        self.workspace_import_button.setEnabled(not import_blocked)
        self.workspace_save_button.setToolTip(save_blocked or '保存板卡、引脚、Flash 设置、方案库、编辑草稿和 DAC 分配为一个配置包。')
        self.workspace_import_button.setToolTip(import_blocked or '导入配置与方案；先备份当前工作区，恢复后保持未连接。')
        self.compile_button.setEnabled(not self.busy and self.profile_valid)
        self.save_button.setEnabled(not self.busy and self.profile_valid)
        self.upload_button.setEnabled(ready and self.profile_valid and state in (0,3))
        self.arm_button.setEnabled(ready and matches and state in (0,3))
        self.trigger_button.setEnabled(ready and state==1 and matches and self.profile.trigger=="software")
        self.external_button.setVisible(self.demo and self.connected)
        self.external_button.setEnabled(ready and state==1 and matches and self.profile.trigger=="external")
        gate=self.timing_mode.currentData()=='gate'
        self.external_button.setText('模拟上升沿' if gate else '模拟所选触发')
        self.falling_button.setVisible(self.demo and self.connected and gate)
        self.falling_button.setEnabled(ready and state==2 and matches and gate)
        self.arrange_actions()
        self.abort_button.setEnabled(ready); self.disarm_button.setEnabled(ready and state==1)
        if self.uploaded_id is not None and not matches: self.download_label.setText("编辑后未下载 · 请重新提交")
        elif matches: self.download_label.setText(f"已下载 · {self.uploaded_id:08X}")
        self.refresh_resources()
        if hasattr(self,'debug_page'):
            initials={i:p.initial for i,row in enumerate(self.parallel_page.rows) for p in self.parallel_page.profiles if p.id==row['combo'].currentData()}
        self.debug_page.set_context(self.caps,self.connected,self.busy,self.last_status,self.demo,initials,self.channel_statuses)
        self.trigger_debug_page.set_context(self.caps,self.connected,self.busy,self.channel_statuses)

    def notice(self,text):
        self.statusBar().showMessage(text,15000)

    def closeEvent(self,event:QCloseEvent):
        if self.board_page.flash_page.running():
            QMessageBox.information(self,"Flash 操作进行中","请等待 Flash 操作结束，避免中断擦写或备份。")
            event.ignore(); return
        if self.board_page.process and self.board_page.process.state()!=QProcess.NotRunning:
            QMessageBox.information(self,"构建进行中","请等待 Vivado 构建结束后关闭，日志会保存在构建目录。")
            event.ignore(); return
        if self.busy:
            QMessageBox.information(self,"设备操作中","请等待当前设备操作返回后关闭。")
            event.ignore(); return
        if not self._exit_saved:
            try:self.session.save(self)
            except (OSError,ValueError,TypeError) as exc:
                self.notice('退出自动保存未完成，窗口已保留')
                QMessageBox.warning(self,'自动保存失败',f'设置未能完整写入，软件暂不退出。请检查磁盘空间和数据目录权限后重试。\n{exc}')
                event.ignore();return
            self._exit_saved=True
            self.centralWidget().setEnabled(False)
        if self.connected:
            self._closing_disconnect=True;self.send('disconnect',None)
            self.notice("正在关闭设备连接…")
            # Let the worker process queued disconnect before exiting its event loop.
            event.ignore(); return
        if not self.resource_monitor.begin_close():
            self.notice('正在结束后台资源检查，完成后自动关闭…');event.ignore();return
        self.resource_monitor.close()
        self.thread.quit()
        if not self.thread.wait(3000):
            QTimer.singleShot(100,self.close)
            event.ignore();return
        event.accept()
        if self._workspace_reopen:
            root,backup=self._workspace_reopen;self._workspace_reopen=None
            self.workspace_reopen.emit(root,backup)

    def finish_close(self,operation,result):
        if operation=='disconnect' and self._closing_disconnect:
            self._closing_disconnect=False;QTimer.singleShot(0,self.close)


def main():
    if sys.platform == "win32":
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("RampStudio.Desktop")
    app=QApplication(sys.argv); app.setApplicationName("Ramp Studio")
    app.setWindowIcon(QIcon(str(Path(__file__).parent / "assets" / "ramp-studio.ico")))
    app.setStyle("Fusion")
    root=None;recovery=''
    while True:
        w=Studio(Library(root) if root else None)
        reopen=[]
        def request_reopen(data_root,backup):
            reopen.append((data_root,backup))
            app.setQuitOnLastWindowClosed(False)
            app.exit(0)
        w.workspace_reopen.connect(request_reopen)
        w.show();app.setQuitOnLastWindowClosed(True)
        if recovery:w.notice(f'全局导入完成 · 设备尚未连接 · 导入前备份：{recovery}')
        code=app.exec()
        if not reopen:break
        root,recovery=reopen[0]
        # Both worker pools have stopped in closeEvent. Destroy the previous
        # widget tree before entering another application event loop.
        from PySide6.QtCore import QCoreApplication,QEvent
        w.deleteLater()
        QCoreApplication.sendPostedEvents(None,QEvent.DeferredDelete)
    sys.exit(code)


if __name__=="__main__": main()
