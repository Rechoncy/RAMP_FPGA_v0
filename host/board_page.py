import json
import sys
import datetime
from pathlib import Path
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QFormLayout, QLineEdit,
    QDoubleSpinBox, QSpinBox, QScrollArea, QComboBox, QCheckBox, QTableWidget, QTableWidgetItem, QHeaderView,
    QPlainTextEdit, QPushButton, QLabel, QFileDialog, QMessageBox, QSplitter, QTabWidget, QLayout)
from PySide6.QtCore import Qt, QProcess, Signal, QTimer, QSignalBlocker
from .board import (Board, ports_for_counts, port_purpose, validate, generate_xdc,
                    ALL_CLOCK_PORTS, SINGLE_CLOCK_STANDARDS, DIFFERENTIAL_CLOCK_STANDARDS)
from .appearance import SoftButton, Foldout, FieldGrid
from .hardware_interfaces import UsbFifoSettings, USB_PORTS, interface_io_standard, apply_davinci_interfaces
from .flash_page import FlashPage
from .runtime import frozen, resource_root
from .utilization_panel import UtilizationPanel
from .uart_settings import BAUD_RATES
from .sample_rates import SAMPLE_RATES, sample_rate_label


class BoardPage(QWidget):
    notification = Signal(str)
    configuration_changed = Signal()
    busy_changed = Signal(bool)

    def __init__(self, library):
        super().__init__()
        self.library = library
        self.board = Board()
        self.process = None
        self.worker_log = None
        self.log_offset = 0
        self.log_timer = QTimer(self)
        self.log_timer.setInterval(150)
        self.log_timer.timeout.connect(self.read_log)
        saved = library.root / "board.json"
        if saved.exists():
            try:
                self.board = Board.from_dict(json.loads(saved.read_text(encoding="utf-8")))
            except Exception as exc:
                self.notification.emit(str(exc))
        self.pin_cache=dict(self.board.pins); self.override_cache=dict(self.board.io_overrides)
        self.timing_cache={k:(str(v.get('min','')),str(v.get('max','')),v.get('falling')) for k,v in self.board.dac_timing.items()}
        outer = QVBoxLayout(self)
        title = QLabel("板卡与连接"); title.setObjectName("title"); outer.addWidget(title)
        desc = QLabel("连接设备、管理板卡配置、分配引脚并生成独立 Vivado 工程。")
        desc.setObjectName("subtitle"); outer.addWidget(desc)
        self.tabs = QTabWidget(); tabs = self.tabs; outer.addWidget(tabs,1)
        config = QWidget(); layout = QVBoxLayout(config)
        layout.setContentsMargins(12,12,12,12); layout.setSpacing(16)
        heading = QLabel('基本信息'); heading.setObjectName('section'); layout.addWidget(heading)
        form = QFormLayout(); form.setVerticalSpacing(12); form.setLabelAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        self.name = QLineEdit(self.board.name); self.part = QLineEdit(self.board.part)
        self.ref = QDoubleSpinBox(); self.ref.setRange(10,500); self.ref.setDecimals(3); self.ref.setSuffix(" MHz"); self.ref.setValue(self.board.ref_mhz)
        self.output_rate=QComboBox()
        for rate in SAMPLE_RATES:self.output_rate.addItem(sample_rate_label(rate),rate)
        self.output_rate.setCurrentIndex(self.output_rate.findData(int(self.board.core_mhz*1e6)))
        self.output_rate.setToolTip('所有 DAC 共用采样率；保存后须重新构建并烧录。连接时以固件回报为准，波形下发时重新离散。')
        self.dac_model=QComboBox();self.dac_model.addItem('通用 DAC · 自行确认速率上限','');self.dac_model.addItem('3PEAK 3PD5651E · 10 bit / 最高 125 MSPS','3PD5651E')
        self.dac_model.setCurrentIndex(self.dac_model.findData(self.board.dac_model))
        self.clock_input=QComboBox();self.clock_input.addItem('单端 · sys_clk','single_ended');self.clock_input.addItem('差分 · sys_clk_p / sys_clk_n','differential')
        self.clock_io=QComboBox();self.clock_diff_term=QCheckBox('启用内部差分终端（DIFF_TERM）')
        self.clock_diff_term.setToolTip('默认关闭；仅在板卡电气设计与 Bank 电压允许时启用，避免与已有终端重复。')
        self.clock_note=QLabel();self.clock_note.setObjectName('subtitle');self.clock_note.setWordWrap(True)
        self.load_clock_controls(self.board)
        self.vivado = QLineEdit(self.board.vivado)
        self.uart_baud=QComboBox()
        for rate in BAUD_RATES:
            self.uart_baud.addItem(f'{rate:,} baud' + (' · 1 M' if rate==1000000 else ''),rate)
        self.uart_baud.setCurrentIndex(self.uart_baud.findData(self.board.uart_baud))
        self.uart_baud.setToolTip('UART 上电默认速率。新版固件在连接时自动适配 9600～1M；日常改速率只需断开、在连接页选择并重连。修改此默认值仍需重新构建。')
        self.standard = QComboBox(); self.standard.addItems(["LVCMOS33", "LVCMOS25", "LVCMOS18"]); self.standard.setCurrentText(self.board.io_standard)
        self.cfgbvs=QComboBox(); self.cfgbvs.addItems(["VCCO","GND"]); self.cfgbvs.setCurrentText(self.board.cfgbvs)
        self.config_voltage=QComboBox(); self.config_voltage.addItems(["3.3","2.5","1.8"]); self.config_voltage.setCurrentText(str(self.board.config_voltage))
        self.dac_bits=QComboBox();
        for bits in (10,12,14): self.dac_bits.addItem(f'{bits} bit · 0～{(1<<bits)-1}',bits)
        self.dac_bits.setCurrentIndex(self.dac_bits.findData(self.board.dac_bits))
        self.previous_bits=self.board.dac_bits; self.logical_pin_cache={}; self.logical_override_cache={}
        self.dac_count=QSpinBox(); self.dac_count.setRange(1,8); self.dac_count.setValue(self.board.dac_count)
        self.trigger_count=QSpinBox(); self.trigger_count.setRange(1,8); self.trigger_count.setValue(self.board.trigger_count)
        form.addRow("板卡名称", self.name); form.addRow("FPGA 型号", self.part)
        form.addRow("Vivado 启动文件", self.vivado)
        layout.addLayout(form)
        heading = QLabel('时钟与通道'); heading.setObjectName('section'); layout.addWidget(heading)
        hardware = QFormLayout(); hardware.setVerticalSpacing(12); hardware.setLabelAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        hardware.addRow('参考时钟输入',self.clock_input)
        frequency=QHBoxLayout();frequency.setSpacing(8);frequency.addWidget(self.ref,1)
        self.clock_presets=[]
        for mhz in (50,100,200):
            button=SoftButton(str(mhz));button.setToolTip(f'设置参考输入为 {mhz} MHz')
            button.clicked.connect(lambda checked=False,value=mhz:self.ref.setValue(value));frequency.addWidget(button);self.clock_presets.append(button)
        hardware.addRow('输入标称频率 / MHz',frequency)
        hardware.addRow('参考时钟 IO 标准',self.clock_io)
        hardware.addRow(self.clock_diff_term)
        hardware.addRow('DAC 型号预设',self.dac_model)
        hardware.addRow('输出采样率（所有通道）',self.output_rate)
        hardware.addRow('DAC 位数（所有通道）',self.dac_bits)
        counts=QHBoxLayout(); counts.setSpacing(12)
        counts.addWidget(QLabel('DAC 数量')); counts.addWidget(self.dac_count,1)
        counts.addWidget(QLabel('触发输入数量')); counts.addWidget(self.trigger_count,1)
        hardware.addRow('硬件扩展',counts); layout.addLayout(hardware)
        layout.addWidget(self.clock_note)
        heading=QLabel('串口通信'); heading.setObjectName('section'); layout.addWidget(heading)
        serial_form=QFormLayout(); serial_form.setVerticalSpacing(12)
        serial_form.addRow('UART 上电默认波特率',self.uart_baud); layout.addLayout(serial_form)
        self.serial_note=QLabel('修改后须重新构建并烧录。连接页波特率应与已烧录固件一致；更改此处不会立即改变板上的通信速率。')
        self.serial_note.setObjectName('subtitle'); self.serial_note.setWordWrap(True); layout.addWidget(self.serial_note)
        self.create_interface_controls(layout)
        heading=QLabel('IO 电气设置'); heading.setObjectName('section'); layout.addWidget(heading)
        electrical=QHBoxLayout(); electrical.setSpacing(12)
        electrical.addWidget(QLabel('统一 IO 标准')); electrical.addWidget(self.standard,1)
        electrical.addWidget(QLabel('CFGBVS')); electrical.addWidget(self.cfgbvs,1)
        electrical.addWidget(QLabel('配置电压 / V')); electrical.addWidget(self.config_voltage,1)
        self.config_voltage.setToolTip('配置 Bank 的 CONFIG_VOLTAGE')
        layout.addLayout(electrical)
        warning=QLabel('IO 电压须与 Bank 电源匹配；更换板卡后逐项核对真实接线。格式校验不等于电气验证。')
        warning.setObjectName('subtitle'); warning.setWordWrap(True); layout.addWidget(warning)
        next_step=QLabel('设置通道数量和位数后，在「引脚配置」分配接线，在「DAC 时序」填写输出时序。')
        next_step.setObjectName('subtitle'); next_step.setWordWrap(True); layout.addWidget(next_step)
        layout.addStretch()
        self.info_page=QScrollArea(); self.info_page.setWidgetResizable(True); self.info_page.setWidget(config)
        self.info_page.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        tabs.addTab(self.info_page,'板卡信息')

        self.pins_page=QWidget(); pins=QVBoxLayout(self.pins_page)
        pins.setContentsMargins(12,12,12,12); pins.setSpacing(12)
        self.port_filter=QComboBox(); self.port_filter.currentIndexChanged.connect(self.filter_ports)
        filter_row=QHBoxLayout(); filter_row.setSpacing(12); filter_row.addWidget(QLabel('信号分组'))
        filter_row.addWidget(self.port_filter,1); pins.addLayout(filter_row)
        pin_note=QLabel('双击管脚或 IO 标准编辑；普通 IO 留空继承统一值，USB 使用独立标准。UART 与 USB FIFO 的引脚分别列出，修改后需重新构建固件。')
        pin_note.setWordWrap(True); pin_note.setObjectName('subtitle'); pins.addWidget(pin_note)
        self.table = QTableWidget(0,4)
        self.table.verticalHeader().setDefaultSectionSize(36)
        self.table.setHorizontalHeaderLabels(["信号", "管脚", "IO 标准（时钟单独设置）", "用途"])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.table.setAlternatingRowColors(True)
        pins.addWidget(self.table,1)
        tabs.addTab(self.pins_page,'引脚配置')

        timing_content=QWidget(); pv=QVBoxLayout(timing_content); pv.setSizeConstraint(QLayout.SetMinimumSize)
        pv.setContentsMargins(12,12,12,12); pv.setSpacing(12)
        heading=QLabel('默认输出时序'); heading.setObjectName('section'); pv.addWidget(heading)
        timing = QFormLayout(); timing.setVerticalSpacing(12); timing.setLabelAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        self.delay_min = QLineEdit("" if self.board.dac_delay_min is None else str(self.board.dac_delay_min))
        self.delay_max = QLineEdit("" if self.board.dac_delay_max is None else str(self.board.dac_delay_max))
        self.delay_min.setPlaceholderText("未确认；单位 ns，例如由保持时间和走线差推导")
        self.delay_max.setPlaceholderText("未确认；单位 ns，由建立时间和走线差推导")
        self.falling = QCheckBox("DAC 在转发时钟下降沿采样"); self.falling.setChecked(self.board.dac_falling)
        timing.addRow("set_output_delay -min", self.delay_min); timing.addRow("set_output_delay -max", self.delay_max); timing.addRow(self.falling)
        pv.addLayout(timing)
        self.diagnostic_timing_button=SoftButton('填入诊断默认值 · 上升沿 / −0.5～1.0 ns')
        self.diagnostic_timing_button.clicked.connect(self.apply_diagnostic_timing)
        pv.addWidget(self.diagnostic_timing_button)
        assumption=QLabel('诊断默认值假设 DAC 建立时间 1.0 ns、保持时间 0.5 ns，未计入未知走线差。保存后可重复使用；换 DAC 或板卡时仍需核对实际参数。未勾选下降沿即为上升沿。逐路覆盖优先于默认值。')
        assumption.setWordWrap(True);assumption.setObjectName('subtitle');pv.addWidget(assumption)
        self.preview_page = QWidget(); v = QVBoxLayout(self.preview_page)
        self.check_label = QLabel(); self.check_label.setWordWrap(True);self.check_label.setAlignment(Qt.AlignTop)
        diagnostics=QScrollArea();diagnostics.setWidgetResizable(True);diagnostics.setWidget(self.check_label)
        diagnostics.setMinimumHeight(80);diagnostics.setMaximumHeight(135);v.addWidget(diagnostics)
        self.xdc = QPlainTextEdit(); self.xdc.setReadOnly(True); v.addWidget(self.xdc)
        tabs.addTab(self.preview_page,"XDC 预览")
        self.build_page = QWidget(); v = QVBoxLayout(self.build_page)
        description = QLabel("生成工程允许保留普通 IO 待填引脚，适合综合检查。实现及生成 bitstream 要求全部引脚和 DAC 延迟完整，并通过工具检查。不会自动烧录开发板。")
        description.setWordWrap(True); v.addWidget(description)
        self.mode = QComboBox(); self.mode.addItem("生成工程与时钟 IP", "generate"); self.mode.addItem("生成并综合", "synth"); self.mode.addItem("核心布局布线检查（允许 XDC 草稿）", "check"); self.mode.addItem("实现与 bitstream", "implement"); v.addWidget(self.mode)
        self.build_button = SoftButton("启动 Vivado 构建"); self.build_button.setObjectName("primary"); self.build_button.clicked.connect(self.build); v.addWidget(self.build_button)
        self.build_log = QPlainTextEdit(); self.build_log.setReadOnly(True); self.build_log.setMaximumBlockCount(5000); v.addWidget(self.build_log)
        tabs.addTab(self.build_page,"构建与日志")
        heading=QLabel('逐路时序覆盖'); heading.setObjectName('section'); pv.addWidget(heading)
        note=QLabel('各 DAC 可单独设置输出延迟与采样边沿；留空继承上方默认值。新增 DAC 需确认实际时序。')
        note.setWordWrap(True); pv.addWidget(note)
        reference=SoftButton('填入 3PD5651E 典型参考 · 上升沿 / −1.5～2.0 ns');reference.clicked.connect(self.apply_dac_reference_timing);pv.addWidget(reference)
        self.timing_table=QTableWidget(0,4); self.timing_table.setHorizontalHeaderLabels(['DAC','最小延迟 / ns','最大延迟 / ns','DAC 采样边沿'])
        self.timing_table.setMinimumHeight(200)
        self.timing_table.verticalHeader().setDefaultSectionSize(48)
        self.timing_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch); pv.addWidget(self.timing_table)
        self.timing_page=QScrollArea(); self.timing_page.setWidgetResizable(True); self.timing_page.setWidget(timing_content)
        self.timing_page.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        tabs.addTab(self.timing_page,'DAC 时序')
        trigger_content=QWidget(); tv=QVBoxLayout(trigger_content);tv.setSizeConstraint(QLayout.SetMinimumSize);tv.setSpacing(18)
        self.trigger_page=QScrollArea();self.trigger_page.setWidgetResizable(True);self.trigger_page.setWidget(trigger_content)
        help_text=QLabel('外部触发接入步骤\n\n1. 在「板卡信息」设置触发输入数量，再到「引脚配置」为 trigger_in[n] 分配真实数字 IO 管脚。\n2. 外部触发信号接该管脚，信号地与 FPGA 板 GND 共地，电平匹配对应 Bank 的 IO 标准。不能将不匹配的电平直接接入。\n3. 生成 XDC 和新固件、完成时序检查并烧录；只在软件里增加输入不会改变板上的固件。\n4. 工作台选择「外部触发」、输入编号、上升/下降沿，然后下载方案并布防。\n5. 布防完成后送入一个新边沿；新版固件的固定时长外部触发模式运行中再次触发会被忽略，完成后回到初始码并自动重新布防（预取完成后接受新边沿）。旧固件需手动布防。电平门控模式：上升沿进入、高电平保持、下降沿返回，完成后自动重新布防；提前下降沿从当前码线性返回。建议高、低电平各至少 20 ns，可靠最短脉宽需板上确认。\n\n「软件触发」不需要触发线，但仍通过 UART 通信；软件按钮不提供确定的外部时序。')
        for step in range(2,6):help_text.setText(help_text.text().replace(f'\n{step}.',f'\n\n{step}.'))
        help_text.setWordWrap(True); tv.addWidget(help_text)
        self.trigger_wiring=QLabel(); self.trigger_wiring.setWordWrap(True); tv.addWidget(self.trigger_wiring); tv.addStretch()
        tabs.addTab(self.trigger_page,'触发接线说明')
        self.flash_page=FlashPage(self); tabs.addTab(self.flash_page,"Flash 固化与启动")
        bar = QHBoxLayout(); bar.setContentsMargins(0,10,0,0); bar.setSpacing(10)
        for label, method in [('导入配置',self.import_board),('导出配置',self.export_board)]:
            button=SoftButton(label); button.clicked.connect(method); bar.addWidget(button)
        bar.addStretch()
        for label, method in [('校验 / 预览',self.preview),('导出 XDC',self.export)]:
            button=SoftButton(label); button.clicked.connect(method); bar.addWidget(button)
        self.save_button=SoftButton('保存板卡'); self.save_button.setObjectName('primary')
        self.save_button.setToolTip('保存全部板卡信息、引脚、DAC 时序和 Flash 设置')
        self.save_button.clicked.connect(self.save); bar.addWidget(self.save_button); outer.addLayout(bar)
        self.rebuild_ports(capture=False)
        self.dac_count.valueChanged.connect(self.rebuild_ports); self.trigger_count.valueChanged.connect(self.rebuild_ports)
        self.dac_bits.currentIndexChanged.connect(self.change_width)
        self.clock_input.currentIndexChanged.connect(self.change_clock_input)
        self.clock_io.currentIndexChanged.connect(self.clock_settings_changed)
        self.clock_diff_term.toggled.connect(self.clock_settings_changed)
        self.ref.valueChanged.connect(self.clock_settings_changed)
        self.output_rate.currentIndexChanged.connect(self.clock_settings_changed)
        self.dac_model.currentIndexChanged.connect(self.clock_settings_changed)
        self.uart_baud.currentIndexChanged.connect(lambda *args:self.configuration_changed.emit())
        self.standard.currentTextChanged.connect(self.clock_settings_changed)
        self.usb_enabled.toggled.connect(self.interface_settings_changed)
        self.usb_standard.currentTextChanged.connect(self.interface_settings_changed)
        self.table.itemChanged.connect(lambda *args:self.configuration_changed.emit())
        self.table.itemChanged.connect(lambda *args:self.update_trigger_wiring())
        self.utilization_panel=UtilizationPanel(self.library.root);self.utilization_panel.setParent(self);self.utilization_panel.hide()
        self.utilization_panel.build_requested.connect(self.open_resource_build)
        self.utilization_timer=QTimer(self);self.utilization_timer.setSingleShot(True);self.utilization_timer.setInterval(250)
        self.utilization_timer.timeout.connect(self.refresh_utilization_context)
        schedule=lambda *args:self.utilization_timer.start()
        self.configuration_changed.connect(schedule)
        for edit in self.findChildren(QLineEdit):edit.textChanged.connect(schedule)
        for combo in self.findChildren(QComboBox):combo.currentIndexChanged.connect(schedule)
        for spin in (*self.findChildren(QSpinBox),*self.findChildren(QDoubleSpinBox)):spin.valueChanged.connect(schedule)
        for check in self.findChildren(QCheckBox):check.toggled.connect(schedule)
        self.timing_table.itemChanged.connect(schedule)
        self.tabs.currentChanged.connect(schedule)
        self.refresh_utilization_context()
        self.preview(switch=False)

    def apply_dac_reference_timing(self):
        self.delay_min.setText('-1.5');self.delay_max.setText('2.0');self.falling.setChecked(False)
        self.configuration_changed.emit();self.preview(switch=False)
        self.notification.emit('已填入 3PD5651E 典型值参考：建立 2 ns / 保持 1.5 ns；仍需计入板级偏斜，不能作为保证值')

    def apply_diagnostic_timing(self):
        self.delay_min.setText('-0.5');self.delay_max.setText('1.0');self.falling.setChecked(False)
        self.configuration_changed.emit()
        self.preview(switch=False)
        self.notification.emit('已填入诊断时序假设；保存配置后保留，实际 DAC / 板级时序仍待核对')

    def create_interface_controls(self, layout):
        heading=QLabel('高速 USB 通信'); heading.setObjectName('section'); layout.addWidget(heading)
        self.usb_enabled=QCheckBox('增加 FT232H USB FIFO 接口，同时保留 UART 串口')
        layout.addWidget(self.usb_enabled)
        self.interface_note=QLabel(); self.interface_note.setWordWrap(True); self.interface_note.setObjectName('subtitle'); layout.addWidget(self.interface_note)
        details=Foldout('USB 接口参数与达芬奇引脚模板'); self.interface_details=details; layout.addWidget(details)
        template_row=QHBoxLayout()
        template_row.addWidget(QLabel('达芬奇 V2.1 · FT232H · 15 根信号'),1)
        button=SoftButton('应用 USB 引脚'); button.clicked.connect(self.apply_interface_template); template_row.addWidget(button)
        jump=SoftButton('查看 USB 引脚'); jump.clicked.connect(lambda:self.open_interface_pins('usb')); template_row.addWidget(jump)
        details.body.addLayout(template_row)
        source=QLabel('模板只补充 USB 接口；遇到现有引脚冲突会拒绝。使用板载 USB SLAVE 插口，串口继续使用 USB UART 插口。波形存储保持片内 BRAM。')
        source.setWordWrap(True); source.setObjectName('subtitle'); details.body.addWidget(source)
        self.usb_fields=QWidget(); usb_layout=QVBoxLayout(self.usb_fields); usb_layout.setContentsMargins(0,0,0,0)
        usb_grid=FieldGrid(columns=2,minimum=190)
        self.usb_standard=QComboBox(); self.usb_standard.addItems(['LVCMOS33','LVCMOS25','LVCMOS18']); usb_grid.add_field('独立 IO 标准',self.usb_standard)
        for field,caption in [('usb_input_min','输入最小延迟 / ns'),('usb_input_max','输入最大延迟 / ns'),('usb_output_min','输出最小延迟 / ns'),('usb_output_max','输出最大延迟 / ns')]:
            edit=QLineEdit(); setattr(self,field,edit); usb_grid.add_field(caption,edit)
        usb_layout.addWidget(usb_grid)
        note=QLabel('USB FIFO 接口时钟 60 MHz；默认输入 0 / 9 ns、输出 0 / 7.5 ns 来自 FT232H 同步 FIFO 时序，更换板卡时需计入走线差。USB 时钟独立于 DAC 输出采样率。')
        note.setWordWrap(True); note.setObjectName('subtitle'); usb_layout.addWidget(note); details.body.addWidget(self.usb_fields)
        self.load_interface_controls(self.board)

    def load_interface_controls(self, board):
        usb=UsbFifoSettings.from_dict(board.usb_fifo)
        blockers=[QSignalBlocker(widget) for widget in (self.usb_enabled,self.usb_standard)]
        self.usb_enabled.setChecked(usb.enabled);self.usb_standard.setCurrentText(usb.io_standard)
        for field,key in [('usb_input_min','input_delay_min'),('usb_input_max','input_delay_max'),('usb_output_min','output_delay_min'),('usb_output_max','output_delay_max')]:
            getattr(self,field).setText(f'{getattr(usb,key):g}')
        self.update_interface_note(); del blockers

    def interface_settings(self):
        return UsbFifoSettings(enabled=self.usb_enabled.isChecked(),io_standard=self.usb_standard.currentText(),
            input_delay_min=float(self.usb_input_min.text()),input_delay_max=float(self.usb_input_max.text()),
            output_delay_min=float(self.usb_output_min.text()),output_delay_max=float(self.usb_output_max.text())).to_dict()

    def update_interface_note(self):
        usb=self.usb_enabled.isChecked()
        self.usb_fields.setEnabled(usb)
        self.uart_baud.setEnabled(True)
        self.serial_note.setText('UART 始终保留。新版固件支持连接时自动适配 9600～1M，断开后选择速率并重连即可。旧固定速率固件需升级一次；USB FIFO 不使用波特率。')
        self.interface_note.setText(('构建 USB FIFO + UART 双接口固件，连接时在设备列表选择其一。' if usb else '构建 UART 固件；可选启用高速 USB。')+'波形仍存于 BRAM，不增加码段最短时长限制。')

    def open_interface_pins(self, group):
        if self.port_filter.findData(group)<0:
            self.notification.emit('请先启用 USB 或应用接口模板，再分配引脚')
            return
        self.port_filter.setCurrentIndex(self.port_filter.findData(group))
        self.tabs.setCurrentWidget(self.pins_page)

    def interface_settings_changed(self,*args):
        self.update_interface_note();self.rebuild_ports()

    def apply_interface_template(self):
        try:
            board=apply_davinci_interfaces(self.collect())
            self.pin_cache=dict(board.pins);self.override_cache=dict(board.io_overrides)
            self.load_interface_controls(board);self.rebuild_ports(capture=False);self.preview(switch=False)
            self.notification.emit('已填入达芬奇 USB 接口引脚；UART、DAC、触发与系统时钟保持原配置')
        except (ValueError,TypeError) as exc:QMessageBox.warning(self,'接口模板未应用',str(exc))

    def refresh_utilization_context(self):
        try:self.utilization_panel.set_board(self.collect())
        except (ValueError,TypeError,KeyError) as exc:self.utilization_panel.set_board(None,str(exc))

    def open_resource_build(self):
        self.mode.setCurrentIndex(self.mode.findData('synth'));self.tabs.setCurrentWidget(self.build_page)

    def load_clock_controls(self,board):
        """Loading a saved board does not reinterpret or guess either clock pin."""
        self.output_rate.blockSignals(True);self.dac_model.blockSignals(True)
        self.output_rate.setCurrentIndex(self.output_rate.findData(int(board.core_mhz*1e6)))
        self.dac_model.setCurrentIndex(self.dac_model.findData(board.dac_model))
        self.output_rate.blockSignals(False);self.dac_model.blockSignals(False)
        self.clock_input.blockSignals(True);self.clock_io.blockSignals(True);self.clock_diff_term.blockSignals(True)
        self.clock_input.setCurrentIndex(self.clock_input.findData(board.clock_input))
        self.previous_clock_input=board.clock_input
        self.clock_standard_cache={'single_ended':'','differential':'LVDS_25'}
        selected=board.clock_io_standard or (board.io_overrides.get('sys_clk','') if board.clock_input=='single_ended' else 'LVDS_25')
        self.clock_standard_cache[board.clock_input]=selected
        self.fill_clock_standards(selected)
        self.clock_diff_term.setChecked(board.clock_diff_term)
        self.clock_input.blockSignals(False);self.clock_io.blockSignals(False);self.clock_diff_term.blockSignals(False)

    def fill_clock_standards(self,selected):
        differential=self.clock_input.currentData()=='differential'
        self.clock_io.clear()
        if not differential:self.clock_io.addItem('继承统一 IO 标准','')
        for standard in (DIFFERENTIAL_CLOCK_STANDARDS if differential else SINGLE_CLOCK_STANDARDS):self.clock_io.addItem(standard,standard)
        self.clock_io.setCurrentIndex(max(0,self.clock_io.findData(selected)))
        self.clock_diff_term.setVisible(differential)

    def change_clock_input(self,*args):
        self.capture_tables()
        self.clock_standard_cache[self.previous_clock_input]=self.clock_io.currentData()
        self.previous_clock_input=self.clock_input.currentData()
        self.clock_io.blockSignals(True);self.fill_clock_standards(self.clock_standard_cache[self.previous_clock_input]);self.clock_io.blockSignals(False)
        self.rebuild_ports(capture=False)
        self.notification.emit('参考时钟形式已更改；在引脚配置填写真实时钟引脚，重新构建后生效')

    def update_clock_note(self):
        differential=self.clock_input.currentData()=='differential'
        source='差分 P/N' if differential else '单端'
        wiring='差分时钟须填写同一对 P/N 引脚；IO 标准与终端按板卡原理图设置。' if differential else '单端输入使用 sys_clk；频率填写板载振荡器的实际标称值。'
        self.clock_note.setText(f'{self.ref.value():g} MHz {source}输入 → 核心 / DAC {self.output_rate.currentData()/1e6:g} MHz · {1e9/self.output_rate.currentData():g} ns/点 · Xilinx 7 系列\n{wiring}\n修改采样率需重新构建、烧录；已有方案时长与原码不变。' + ('\n3PD5651E 仅支持 100 / 125 MSPS、10 位；200 MSPS 配置将被拒绝。' if self.dac_model.currentData() else ''))

    def clock_settings_changed(self,*args):
        self.update_clock_note()
        standard=self.clock_io.currentData() or self.standard.currentText()
        self.table.blockSignals(True)
        for row in range(self.table.rowCount()):
            if self.table.item(row,0).text() in ALL_CLOCK_PORTS:self.table.item(row,2).setText(standard)
        self.table.blockSignals(False)
        self.configuration_changed.emit()

    def capture_tables(self):
        for i in range(self.table.rowCount()):
            port=self.table.item(i,0).text()
            self.pin_cache[port]=self.table.item(i,1).text().strip().upper()
            if port not in ALL_CLOCK_PORTS:self.override_cache[port]=self.table.item(i,2).text().strip().upper()
        for i in range(self.timing_table.rowCount()):
            self.timing_cache[str(i)]=(self.timing_table.item(i,1).text().strip(),self.timing_table.item(i,2).text().strip(),self.timing_table.cellWidget(i,3).currentData())

    def rebuild_ports(self,*args,capture=True):
        if capture: self.capture_tables()
        ports=ports_for_counts(self.dac_count.value(),self.trigger_count.value(),self.dac_bits.currentData(),self.clock_input.currentData())
        if self.usb_enabled.isChecked():ports.extend(USB_PORTS)
        self.table.blockSignals(True); self.table.setRowCount(len(ports))
        for i,port in enumerate(ports):
            standard=(self.clock_io.currentData() or self.standard.currentText()) if port in ALL_CLOCK_PORTS else self.override_cache.get(port,'')
            for j,text in enumerate([port,self.pin_cache.get(port,''),standard,port_purpose(port,self.dac_bits.currentData())]):
                item=QTableWidgetItem(text)
                if j==2 and port in USB_PORTS:
                    fallback=interface_io_standard(Board(usb_fifo={'io_standard':self.usb_standard.currentText()}),port)
                    item.setToolTip(f'留空采用 USB 接口标准 {fallback}')
                if j in (0,3) or j==2 and port in ALL_CLOCK_PORTS:item.setFlags(item.flags() & ~Qt.ItemIsEditable)
                self.table.setItem(i,j,item)
        self.table.blockSignals(False)
        current=self.port_filter.currentData()
        self.port_filter.blockSignals(True); self.port_filter.clear(); self.port_filter.addItem('全部信号','all');self.port_filter.addItem('参考时钟','clock'); self.port_filter.addItem('触发输入','trigger'); self.port_filter.addItem('通信与其他','other')
        if self.usb_enabled.isChecked():self.port_filter.addItem('高速 USB FIFO · 15 根','usb')
        for i in range(self.dac_count.value()): self.port_filter.addItem(f'DAC {i+1}',i)
        self.port_filter.setCurrentIndex(max(0,self.port_filter.findData(current))); self.port_filter.blockSignals(False); self.filter_ports()
        self.timing_table.setRowCount(self.dac_count.value())
        for i in range(self.dac_count.value()):
            low,high,falling=self.timing_cache.get(str(i),('','',None))
            item=QTableWidgetItem(f'DAC {i+1}'); item.setFlags(item.flags() & ~Qt.ItemIsEditable); self.timing_table.setItem(i,0,item)
            self.timing_table.setItem(i,1,QTableWidgetItem(low)); self.timing_table.setItem(i,2,QTableWidgetItem(high))
            edge=QComboBox(); edge.addItem('继承默认',None); edge.addItem('上升沿',False); edge.addItem('下降沿',True)
            edge.setCurrentIndex(0 if falling is None else 2 if falling else 1); self.timing_table.setCellWidget(i,3,edge)
            edge.currentIndexChanged.connect(lambda *args:self.configuration_changed.emit())
        self.update_clock_note();self.update_trigger_wiring(); self.configuration_changed.emit()

    def change_width(self,*args):
        self.capture_tables()
        bits=self.dac_bits.currentData()
        for field,cache in [('pin_cache',self.logical_pin_cache),('override_cache',self.logical_override_cache)]:
            old=getattr(self,field)
            for port,value in old.items():
                if port.startswith('dac_data['): cache[divmod(int(port[9:-1]),self.previous_bits)]=value
            updated={p:v for p,v in old.items() if not p.startswith('dac_data[')}
            updated.update({f'dac_data[{channel*bits+bit}]':value for (channel,bit),value in cache.items() if bit<bits})
            setattr(self,field,updated)
        self.previous_bits=bits
        self.rebuild_ports(capture=False)
        self.notification.emit('DAC 位数已更改；原码不缩放，新增数据位需分配管脚并重新构建、烧录')

    def update_trigger_wiring(self):
        mapping={self.table.item(i,0).text():self.table.item(i,1).text() for i in range(self.table.rowCount())}
        self.trigger_wiring.setText('当前引脚配置（需重新构建生效）：\n'+'\n'.join(f'输入 {i+1} · trigger_in[{i}] → {mapping.get(f"trigger_in[{i}]", "") or "待分配"}' for i in range(self.trigger_count.value())))

    def filter_ports(self,*args):
        if not hasattr(self,'table'): return
        group=self.port_filter.currentData()
        for i in range(self.table.rowCount()):
            port=self.table.item(i,0).text()
            match=(group=='all' or group=='clock' and port in ALL_CLOCK_PORTS or group=='trigger' and port.startswith('trigger_in') or group=='other' and port not in ALL_CLOCK_PORTS and not port.startswith(('dac_','trigger_in','usb_'))
                   or group=='usb' and port in USB_PORTS)
            if type(group) is int:
                match=port==f'dac_clk[{group}]' or port.startswith('dac_data[') and int(port[9:-1])//self.dac_bits.currentData()==group
            self.table.setRowHidden(i,not match)

    def collect(self):
        self.capture_tables()
        usb_fifo=self.interface_settings()
        timing={}
        for key,(low,high,falling) in self.timing_cache.items():
            if low or high or falling is not None:
                item={}
                if low: item['min']=float(low)
                if high: item['max']=float(high)
                if falling is not None: item['falling']=falling
                timing[key]=item
        b = Board(name=self.name.text().strip(), part=self.part.text().strip(), ref_mhz=self.ref.value(),core_mhz=self.output_rate.currentData()/1e6,dac_model=self.dac_model.currentData(),
                  vivado=self.vivado.text().strip(), io_standard=self.standard.currentText(),uart_baud=self.uart_baud.currentData(),
                  cfgbvs=self.cfgbvs.currentText(),config_voltage=float(self.config_voltage.currentText()),
                  pins=dict(self.pin_cache), dac_bits=self.dac_bits.currentData(), dac_count=self.dac_count.value(),trigger_count=self.trigger_count.value(),dac_timing=timing,
                  io_overrides={p:v for p,v in self.override_cache.items() if v and p not in ALL_CLOCK_PORTS},
                  clock_input=self.clock_input.currentData(),clock_io_standard=self.clock_io.currentData(),
                  clock_diff_term=self.clock_input.currentData()=='differential' and self.clock_diff_term.isChecked(),
                  dac_delay_min=float(self.delay_min.text()) if self.delay_min.text().strip() else None,
                  dac_delay_max=float(self.delay_max.text()) if self.delay_max.text().strip() else None,
                  dac_falling=self.falling.isChecked(),flash=self.flash_page.settings().to_dict(),boot_profiles=dict(self.flash_page.boot_profiles),
                  usb_fifo=usb_fifo)
        return b

    # Session drafts retain the literal editor text, including unfinished values
    # which cannot yet be represented by the validated Board configuration.
    _SESSION_FIELDS = ('name', 'part', 'vivado', 'ref', 'output_rate', 'dac_model', 'uart_baud', 'clock_input', 'clock_io',
                       'clock_diff_term', 'standard', 'cfgbvs', 'config_voltage',
                       'dac_bits', 'dac_count', 'trigger_count', 'delay_min',
                       'delay_max', 'falling', 'mode', 'usb_enabled', 'usb_standard',
                       'usb_input_min', 'usb_input_max', 'usb_output_min', 'usb_output_max')
    _FLASH_SESSION_FIELDS = ('part', 'size', 'interface', 'rate', 'falling',
                            'backup', 'server', 'target', 'device', 'bit', 'expected')

    @staticmethod
    def _capture_control(widget):
        if isinstance(widget, (QSpinBox, QDoubleSpinBox)):
            return {'value': widget.value(), 'text': widget.lineEdit().text()}
        if isinstance(widget, QComboBox):
            return {'data': widget.currentData(), 'text': widget.currentText()}
        if isinstance(widget, QCheckBox):
            return widget.isChecked()
        return widget.text()

    @staticmethod
    def _restore_control(widget, value):
        if isinstance(widget, (QSpinBox, QDoubleSpinBox)):
            if isinstance(value, dict) and type(value.get('value')) in (int, float):
                widget.setValue(value['value'])
                if isinstance(value.get('text'), str):
                    widget.lineEdit().setText(value['text'])
        elif isinstance(widget, QComboBox):
            if not isinstance(value, dict):
                return
            if widget.isEditable() and isinstance(value.get('text'), str):
                widget.setCurrentText(value['text'])
            else:
                index = widget.findData(value['data']) if value.get('data') is not None else -1
                if index < 0 and isinstance(value.get('text'), str):
                    index = widget.findText(value['text'])
                if index >= 0:
                    widget.setCurrentIndex(index)
        elif isinstance(widget, QCheckBox):
            if type(value) is bool:
                widget.setChecked(value)
        elif isinstance(value, str):
            widget.setText(value)

    def capture_session(self):
        """Return a JSON-safe editing snapshot without validating or writing it."""
        pins, overrides = dict(self.pin_cache), dict(self.override_cache)
        timing = {key: list(value) for key, value in self.timing_cache.items()}
        for row in range(self.table.rowCount()):
            port = self.table.item(row, 0).text()
            pins[port] = self.table.item(row, 1).text()
            if port not in ALL_CLOCK_PORTS:
                overrides[port] = self.table.item(row, 2).text()
        for row in range(self.timing_table.rowCount()):
            timing[str(row)] = [self.timing_table.item(row, 1).text(),
                                self.timing_table.item(row, 2).text(),
                                self.timing_table.cellWidget(row, 3).currentData()]
        standards = dict(self.clock_standard_cache)
        standards[self.clock_input.currentData()] = self.clock_io.currentData()
        return {
            'schema': 1,
            'fields': {name: self._capture_control(getattr(self, name))
                       for name in self._SESSION_FIELDS},
            'pins': pins, 'io_overrides': overrides, 'dac_timing': timing,
            'logical_pins': [[channel, bit, value]
                             for (channel, bit), value in self.logical_pin_cache.items()],
            'logical_io_overrides': [[channel, bit, value]
                                     for (channel, bit), value in self.logical_override_cache.items()],
            'clock_standards': standards,
            'flash': {name: self._capture_control(getattr(self.flash_page, name))
                      for name in self._FLASH_SESSION_FIELDS},
            'boot_profiles': json.loads(json.dumps(self.flash_page.boot_profiles)),
            'boot_selected': [check.isChecked() for check in self.flash_page.boot_checks],
            'tab': self.tabs.currentIndex(), 'port_filter': self._capture_control(self.port_filter),
        }

    def restore_session(self, data):
        """Restore a local draft silently; never save, build, connect or program."""
        if not isinstance(data, dict) or data.get('schema') != 1:
            return False
        # Block our application signals, not Qt's private tab bar / stack
        # signals: those must run to keep currentIndex and currentWidget aligned.
        controls = [self, self.tabs, self.table, self.timing_table, self.port_filter,
                    self.utilization_panel, self.flash_page, *self.flash_page.boot_checks]
        controls += [getattr(self, name) for name in self._SESSION_FIELDS]
        controls += [getattr(self.flash_page, name) for name in self._FLASH_SESSION_FIELDS]
        blockers = [QSignalBlocker(widget) for widget in controls]
        self.utilization_timer.stop()
        try:
            fields = data.get('fields', {})
            if isinstance(fields, dict):
                for name in self._SESSION_FIELDS:
                    if name != 'clock_io' and name in fields:
                        self._restore_control(getattr(self, name), fields[name])
                self.fill_clock_standards('')
                if 'clock_io' in fields:
                    self._restore_control(self.clock_io, fields['clock_io'])
            self.update_interface_note()
            self.previous_bits = self.dac_bits.currentData()
            self.previous_clock_input = self.clock_input.currentData()
            for field, attribute in (('pins', 'pin_cache'), ('io_overrides', 'override_cache')):
                values = data.get(field)
                if isinstance(values, dict):
                    setattr(self, attribute, {key: value for key, value in values.items()
                                              if isinstance(key, str) and isinstance(value, str)})
            timing = data.get('dac_timing')
            if isinstance(timing, dict):
                self.timing_cache = {key: tuple(value) for key, value in timing.items()
                                     if isinstance(key, str) and isinstance(value, list) and len(value) == 3
                                     and all(isinstance(v, str) for v in value[:2])
                                     and (value[2] is None or type(value[2]) is bool)}
            for field, attribute in (('logical_pins', 'logical_pin_cache'),
                                     ('logical_io_overrides', 'logical_override_cache')):
                values = data.get(field)
                if isinstance(values, list):
                    setattr(self, attribute, {(value[0], value[1]): value[2] for value in values
                                              if isinstance(value, list) and len(value) == 3
                                              and type(value[0]) is int and 0 <= value[0] < 8
                                              and type(value[1]) is int and 0 <= value[1] < 14
                                              and isinstance(value[2], str)})
            standards = data.get('clock_standards')
            if isinstance(standards, dict):
                for kind in self.clock_standard_cache:
                    if isinstance(standards.get(kind), str):
                        self.clock_standard_cache[kind] = standards[kind]
            self.rebuild_ports(capture=False)
            self._restore_control(self.port_filter, data.get('port_filter'))
            self.filter_ports()
            flash = data.get('flash')
            if isinstance(flash, dict):
                for name in self._FLASH_SESSION_FIELDS:
                    if name in flash:
                        self._restore_control(getattr(self.flash_page, name), flash[name])
            profiles = data.get('boot_profiles')
            if isinstance(profiles, dict) and all(isinstance(v, dict) for v in profiles.values()):
                previous = self.flash_page.boot_profiles
                try:
                    self.flash_page.boot_profiles = json.loads(json.dumps(profiles))
                    self.flash_page.show_boot()
                except (ValueError, TypeError, KeyError):
                    self.flash_page.boot_profiles = previous
                    self.flash_page.show_boot()
            selected = data.get('boot_selected')
            if isinstance(selected, list):
                for check, value in zip(self.flash_page.boot_checks, selected):
                    if type(value) is bool:
                        check.setChecked(value)
            self.flash_page.refresh_selection()
            self.flash_page.refresh()
            if type(data.get('tab')) is int and 0 <= data['tab'] < self.tabs.count():
                self.tabs.setCurrentIndex(data['tab'])
            self.preview(switch=False)
            self.refresh_utilization_context()
        finally:
            self.utilization_timer.stop()
            del blockers
        return True

    def valid_settings(self):
        """Return a valid Board, or None for an unfinished draft; do not write."""
        if any(not widget.hasAcceptableInput() for widget in (self.ref, self.dac_count, self.trigger_count)):
            return None
        try:
            board = self.collect()
            return board if not validate(board) else None
        except (ValueError, TypeError, KeyError, OverflowError):
            return None

    def save(self):
        try:
            b = self.collect()
            errors = validate(b)
            if errors: raise ValueError("\n".join(errors))
            path = self.library.root / "board.json"
            temp = path.with_suffix(".tmp")
            temp.write_text(json.dumps(b.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8"); temp.replace(path)
            self.board=b; self.configuration_changed.emit(); self.notification.emit("板卡配置已保存；硬件数量/引脚变更需要重新构建和烧录");return True
        except Exception as exc: QMessageBox.warning(self,"板卡配置",str(exc));return False

    def preview(self, checked=False, switch=True):
        try:
            b = self.collect(); text = generate_xdc(b)
            missing = validate(b, complete=True)
            self.check_label.setText("待完成：\n"+"\n".join(missing) if missing else "结构校验通过。真实管脚与 IO 时序仍以原理图和 Vivado 检查为准。")
            self.xdc.setPlainText(text)
        except Exception as exc:
            self.check_label.setText(str(exc)); self.xdc.clear()
        self.update_trigger_wiring()
        if switch: self.tabs.setCurrentWidget(self.preview_page)

    def export(self):
        try:
            text = generate_xdc(self.collect())
            path,_ = QFileDialog.getSaveFileName(self,"导出 XDC 草稿", "ramp_board.xdc", "XDC (*.xdc)")
            if path: Path(path).write_text(text,encoding="utf-8"); self.notification.emit("XDC 已导出；未完成项保留为 TODO")
        except Exception as exc: QMessageBox.warning(self,"XDC",str(exc))

    def import_board(self):
        path,_ = QFileDialog.getOpenFileName(self,"导入板卡 JSON", "", "JSON (*.json)")
        if not path: return
        try:
            b = Board.from_dict(json.loads(Path(path).read_text(encoding="utf-8")))
            if validate(b): raise ValueError("\n".join(validate(b)))
            self.flash_page.load(b.flash)
            self.flash_page.boot_profiles=dict(b.boot_profiles);self.flash_page.show_boot()
            self.name.setText(b.name); self.part.setText(b.part); self.ref.setValue(b.ref_mhz); self.vivado.setText(b.vivado)
            self.uart_baud.setCurrentIndex(self.uart_baud.findData(b.uart_baud))
            self.standard.setCurrentText(b.io_standard); self.falling.setChecked(b.dac_falling)
            self.cfgbvs.setCurrentText(b.cfgbvs); self.config_voltage.setCurrentText(str(b.config_voltage))
            self.delay_min.setText("" if b.dac_delay_min is None else str(b.dac_delay_min)); self.delay_max.setText("" if b.dac_delay_max is None else str(b.dac_delay_max))
            self.dac_bits.blockSignals(True); self.dac_bits.setCurrentIndex(self.dac_bits.findData(b.dac_bits)); self.dac_bits.blockSignals(False)
            self.previous_bits=b.dac_bits; self.logical_pin_cache={}; self.logical_override_cache={}
            self.board=b; self.pin_cache=dict(b.pins); self.override_cache=dict(b.io_overrides);self.load_clock_controls(b)
            self.load_interface_controls(b)
            self.timing_cache={k:(str(v.get('min','')),str(v.get('max','')),v.get('falling')) for k,v in b.dac_timing.items()}
            self.dac_count.blockSignals(True); self.trigger_count.blockSignals(True)
            self.dac_count.setValue(b.dac_count); self.trigger_count.setValue(b.trigger_count)
            self.dac_count.blockSignals(False); self.trigger_count.blockSignals(False); self.rebuild_ports(capture=False)
            self.preview()
        except Exception as exc: QMessageBox.warning(self,"导入失败",str(exc))

    def export_board(self):
        try:
            b=self.collect()
            if validate(b): raise ValueError("\n".join(validate(b)))
            path,_=QFileDialog.getSaveFileName(self,"导出板卡配置","board.json","JSON (*.json)")
            if path: Path(path).write_text(json.dumps(b.to_dict(),ensure_ascii=False,indent=2),encoding="utf-8")
        except Exception as exc: QMessageBox.warning(self,"导出失败",str(exc))

    def build(self):
        try:
            if self.flash_page.running():raise ValueError("Flash 操作进行中，请等待结束")
            b=self.collect(); mode=self.mode.currentData()
            errors=validate(b,complete=mode=="implement")
            if errors: raise ValueError("\n".join(errors))
            if not Path(b.vivado).is_file(): raise ValueError("Vivado 路径不存在")
            stamp=datetime.datetime.now().strftime("%Y%m%d_%H%M%S_%f")
            root=resource_root()
            config=self.library.root/f"build_{stamp}.json"
            config.write_text(json.dumps(b.to_dict(),ensure_ascii=False,indent=2),encoding="utf-8")
            output=self.library.root/"builds"/f"studio_{stamp}"
            self.build_output=output;self.build_mode=mode
            output.parent.mkdir(parents=True,exist_ok=True)
            self.process=QProcess(self); self.process.setWorkingDirectory(str(root)); self.process.setProcessChannelMode(QProcess.MergedChannels)
            self.process.stateChanged.connect(lambda state:self.busy_changed.emit(state!=QProcess.NotRunning))
            self.process.readyReadStandardOutput.connect(self.read_log)
            self.process.finished.connect(self.build_finished)
            self.process.errorOccurred.connect(self.build_error)
            self.build_log.setPlainText(f"输出目录：{output}\n")
            self.build_button.setEnabled(False)
            python=Path(sys.executable)
            arguments=["--board",str(config),"--output",str(output),"--mode",mode]
            if frozen():
                self.worker_log=self.library.root/f"build_{stamp}.log"
                self.log_offset=0
                arguments=["--build-worker","--worker-log",str(self.worker_log)]+arguments
                self.log_timer.start()
            else:
                self.worker_log=None
                if python.name.lower()=="pythonw.exe": python=python.with_name("python.exe")
                arguments=["-u","-m","host.build_worker"]+arguments
            self.process.start(str(python),arguments)
        except Exception as exc: QMessageBox.warning(self,"无法构建",str(exc))

    def read_log(self):
        text=bytes(self.process.readAllStandardOutput()).decode("utf-8",errors="replace")
        if self.worker_log and self.worker_log.exists():
            with self.worker_log.open("rb") as stream:
                stream.seek(self.log_offset)
                raw=stream.read()
            # Retain an incomplete UTF-8 character until the next polling interval.
            import codecs
            decoder=codecs.getincrementaldecoder("utf-8")(errors="replace")
            text+=decoder.decode(raw,final=False)
            self.log_offset+=len(raw)-len(decoder.getstate()[0])
        cursor=self.build_log.textCursor(); cursor.movePosition(cursor.MoveOperation.End); cursor.insertText(text); self.build_log.setTextCursor(cursor)

    def build_finished(self, code, status):
        self.log_timer.stop()
        self.read_log(); self.build_button.setEnabled(True)
        self.build_log.appendPlainText(f"\n构建{'完成' if code==0 else '失败'}，退出码 {code}")
        self.notification.emit(f"Vivado 构建{'完成' if code==0 else '失败，请查看日志'}")
        self.refresh_utilization_context()
        self.utilization_panel.refresh_latest()
        if code==0 and getattr(self,'build_mode','')=='implement':
            bit=self.build_output/'ramp_studio.bit'
            if bit.is_file():
                self.flash_page.bit.setText(str(bit));self.flash_page.expected.clear()
                self.build_log.appendPlainText('比特流已填入 Flash 页面；请核对后执行固化与校验。')

    def build_error(self,error):
        self.build_log.appendPlainText(f"进程错误：{error}")
        if error==QProcess.FailedToStart:
            self.log_timer.stop(); self.build_button.setEnabled(True)
