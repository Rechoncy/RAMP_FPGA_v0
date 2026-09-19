"""Compact resource status shared by designers and output pages."""
from PySide6.QtWidgets import QWidget,QVBoxLayout,QLabel,QProgressBar,QSizePolicy


def usage_text(report,source,present=True):
    if not present:return f'{source} · 选择方案后自动检查资源'
    if report is None:return f'{source} · 正在计算所需资源…'
    return f'{source} · '+report.summary()


def usage_color(report):
    return '#b43f48' if report is not None and not report.fits else '#1f6da8' if report else '#647c9e'


class ResourceUsageView(QWidget):
    def __init__(self,parent=None):
        super().__init__(parent)
        box=QVBoxLayout(self);box.setContentsMargins(0,3,0,3);box.setSpacing(5)
        self.label=QLabel();self.label.setWordWrap(True);self.label.setSizePolicy(QSizePolicy.Ignored,QSizePolicy.Preferred);box.addWidget(self.label)
        self.bar=QProgressBar();self.bar.setRange(0,1000);self.bar.setTextVisible(False);self.bar.setFixedHeight(5);box.addWidget(self.bar)
        self.set_report(None,'离线估算',False)

    def set_report(self,report,source,present=True):
        self.label.setText(usage_text(report,source,present));color=usage_color(report)
        self.label.setStyleSheet(f'color:{color};font-size:11px;')
        self.setToolTip('显示本地方案的完整离散结果；每路容量独立，重复播放不累加占用。下发前会重新读取设备能力。')
        ratios=[]
        if report:
            for needed,total in [(report.required_runs,report.max_runs),(report.required_steps,report.max_steps),(report.required_segments,report.max_segments)]:
                if needed is not None and total>0:ratios.append(needed/total)
        self.bar.setVisible(bool(ratios) and present)
        self.bar.setValue(min(1000,round(max(ratios,default=0)*1000)))
        self.bar.setStyleSheet(f'QProgressBar{{border:0;border-radius:2px;background:#e8eef8;}} QProgressBar::chunk{{border-radius:2px;background:{color};}}')
