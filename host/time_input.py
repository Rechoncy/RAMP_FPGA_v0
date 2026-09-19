"""Unit-aware editors; public values remain in the model's canonical unit."""
from decimal import Decimal
import math
from PySide6.QtCore import Signal
from PySide6.QtWidgets import QWidget,QHBoxLayout,QDoubleSpinBox,QComboBox

MS_PER_UNIT={'ns':Decimal('0.000001'),'µs':Decimal('0.001'),'ms':Decimal(1),'s':Decimal(1000)}


def display_time(milliseconds):
    value=abs(milliseconds)
    unit='s' if value>=1000 else 'ms' if value>=1 or value==0 else 'µs' if value>=.001 else 'ns'
    return milliseconds/float(MS_PER_UNIT[unit]),unit


class CompactSpinBox(QDoubleSpinBox):
    def textFromValue(self,value):
        text=super().textFromValue(value)
        if self.locale().decimalPoint() in text:
            text=text.rstrip('0').removesuffix(self.locale().decimalPoint())
        return text


class TimeInput(QWidget):
    valueChanged=Signal(float)
    unitChanged=Signal(str)

    def __init__(self,parent=None,*,base_unit='ms',minimum=0,maximum=1000):
        super().__init__(parent)
        self.base_unit=base_unit; self._value=float(minimum); self._minimum=minimum; self._maximum=maximum
        self.spin=CompactSpinBox(); self.spin.setKeyboardTracking(False); self.spin.setMinimumWidth(80)
        self.unit=QComboBox()
        for name in MS_PER_UNIT:self.unit.addItem(name,name)
        self.unit.setObjectName('timeUnit'); self.unit.setFixedWidth(88)
        self.unit.setToolTip('切换单位保留填写数值，实际时长按新单位计算')
        layout=QHBoxLayout(self); layout.setContentsMargins(0,0,0,0); layout.setSpacing(5)
        layout.addWidget(self.spin,1); layout.addWidget(self.unit)
        self.unit.setCurrentIndex(self.unit.findData(base_unit))
        self._render()
        self.spin.valueChanged.connect(self._edited)
        self.unit.currentIndexChanged.connect(self._unit_changed)

    def value(self):return self._value

    def setValue(self,value):
        value=float(value)
        if not math.isfinite(value):raise ValueError('时间必须是有限数值')
        changed=value!=self._value
        self._value=value; self._render()
        if changed:self.valueChanged.emit(value)

    def setUnit(self,unit,*,preserve_duration=False):
        if unit=='μs':unit='µs'
        index=self.unit.findData(unit)
        if index<0:raise ValueError('不支持的时间单位')
        if preserve_duration:
            # Restoring a saved unit is presentation only; the saved model
            # already contains the duration in the canonical unit.
            changed=index!=self.unit.currentIndex()
            blocked=self.unit.blockSignals(True)
            self.unit.setCurrentIndex(index);self.unit.blockSignals(blocked)
            self._render()
            if changed:self.unitChanged.emit(self.unit.currentData())
        else:self.unit.setCurrentIndex(index)

    def _render(self,number=None):
        name=self.unit.currentData()
        factor=MS_PER_UNIT[self.base_unit]/MS_PER_UNIT[name]
        if number is None:number=float(Decimal(str(self._value))*factor)
        blocked=self.spin.blockSignals(True)
        # Keep the same precision across units, including fractional ns.
        self.spin.setDecimals(12)
        # Unit changes can make the duration invalid. Keep the entered number
        # visible for correction; the waveform compiler guards save/download.
        self.spin.setRange(min(number,float(Decimal(str(self._minimum))*factor)),
                           max(number,float(Decimal(str(self._maximum))*factor)))
        self.spin.setSingleStep({'ns':5,'µs':1,'ms':1,'s':.001}[name])
        self.spin.setValue(number)
        self.spin.blockSignals(blocked)

    def _unit_changed(self):
        blocked=self.spin.blockSignals(True)
        self.spin.interpretText()
        number=self.spin.value()
        self.spin.blockSignals(blocked)
        converted=self._canonical(number)
        changed=converted!=self._value
        self._value=converted;self._render(number)
        if changed:self.valueChanged.emit(converted)
        self.unitChanged.emit(self.unit.currentData())

    def _canonical(self,number):
        return float(Decimal(str(number))*MS_PER_UNIT[self.unit.currentData()]/MS_PER_UNIT[self.base_unit])

    def _edited(self,value):
        converted=self._canonical(value)
        if converted!=self._value:
            self._value=converted; self.valueChanged.emit(converted)
