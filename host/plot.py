from itertools import chain
from dataclasses import replace
from PySide6.QtWidgets import QWidget
from PySide6.QtGui import QPainter, QColor, QPen, QPainterPath, QLinearGradient
from PySide6.QtCore import Qt, QRectF
from .model import compile_profile,Capabilities
from .plot_scale import code_axis


class WaveformPlot(QWidget):
    def __init__(self):
        super().__init__()
        self.profile = None
        self.caps = Capabilities()
        self.status = None
        self.y_axis = None
        self.setMinimumHeight(215)
        self.setMouseTracking(True)

    def set_profile(self, profile):
        self.profile = profile
        self.y_axis = None
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        area = QRectF(55, 22, max(10, self.width() - 80), max(10, self.height() - 60))
        p.fillRect(self.rect(), QColor("white"))
        if not self.profile:
            p.drawText(area, Qt.AlignCenter, "添加波形段以预览")
            return
        try:
            profile=self.profile
            gate=profile.timing_mode=='gate'
            if gate:
                from .gating import preview_profile
                profile=preview_profile(profile,self.caps.core_hz)
            c = compile_profile(profile,self.caps if profile.mode!="sequence" else replace(self.caps,sequence_steps=64,infinite_repeat=True))
        except ValueError:
            p.drawText(area, Qt.AlignCenter, "请修正参数后预览")
            return
        total = c.duration_ms
        if self.profile.mode=="sequence":
            from .sequences import preview_sequence
            sequence_preview=preview_sequence(self.profile,self.caps.core_hz);total=sequence_preview[3]
        sampled=self.profile.mode in ('ramp','function','sequence')
        if sampled:
            from .functions import preview_samples
            try: times,raw,codes,_=sequence_preview if self.profile.mode=='sequence' else preview_samples(self.profile,self.caps.core_hz)
            except ValueError:
                p.drawText(area,Qt.AlignCenter,'函数参数待修正'); return
            ending=int(codes[0]) if self.profile.repeat==0 else self.profile.initial
            self.y_axis=code_axis(chain(raw,codes,[ending]),self.caps.dac_bits)
        else:
            self.y_axis=code_axis(chain([self.profile.initial],(s.target for s in self.profile.segments)),self.caps.dac_bits)
        axis=self.y_axis
        staged=gate or self.profile.mode=='function' and self.profile.function_layout=='staged'
        events=self.profile.mode=='sequence' and self.profile.sequence_mode=='events'
        # Keep phase labels above the data, including full-scale waveforms.
        top=49 if staged or events else 27
        area=QRectF(62,top,max(10,self.width()-88),max(10,self.height()-top-42))
        from .time_input import display_time
        axis_total,axis_unit=display_time(total)
        def point(t, value):
            return area.left() + t / total * area.width(), area.bottom() - (value-axis.minimum) / axis.span * area.height()
        p.setPen(QPen(QColor("#e9eff9"), 1))
        for value in axis.ticks:
            y = point(0,value)[1]
            p.drawLine(area.left(), y, area.right(), y)
            p.setPen(QColor("#7186a3"))
            p.drawText(QRectF(0, y - 10, 52, 20), Qt.AlignRight | Qt.AlignVCenter, str(value))
            p.setPen(QPen(QColor("#e9eff9"), 1))
        for i in range(6):
            x = area.left() + i * area.width() / 5
            p.drawLine(x, area.top(), x, area.bottom())
            p.setPen(QColor("#7186a3"))
            p.drawText(QRectF(x-35, area.bottom()+8, 70, 20), Qt.AlignCenter, f"{axis_total*i/5:.3g}")
            p.setPen(QPen(QColor("#e9eff9"), 1))
        requested = QPainterPath(); path = QPainterPath()
        p.save();p.setClipRect(area.adjusted(-2,-2,2,2))
        if sampled:
            requested.moveTo(*point(float(times[0]),float(raw[0])))
            path.moveTo(*point(float(times[0]),int(codes[0])))
            previous=int(codes[0])
            for t,r,code in zip(times[1:],raw[1:],codes[1:]):
                requested.lineTo(*point(float(t),float(r)))
                path.lineTo(*point(float(t),previous)); path.lineTo(*point(float(t),int(code))); previous=int(code)
            path.lineTo(*point(total,previous)); path.lineTo(*point(total,ending))
            p.setPen(QPen(QColor("#96afd5"),1.5,Qt.DashLine)); p.drawPath(requested)
        else:
            requested = QPainterPath()
            requested.moveTo(*point(0, self.profile.initial))
            t = 0
            for s in self.profile.segments:
                t += s.duration_ms
                requested.lineTo(*point(t, s.target))
            p.setPen(QPen(QColor("#96afd5"), 1.5, Qt.DashLine))
            p.drawPath(requested)
            path = QPainterPath()
            path.moveTo(*point(0, self.profile.initial))
            t = 0
            start = self.profile.initial
            for s, n in zip(self.profile.segments, c.counts):
                duration = n*c.divider*1000/self.caps.core_hz
                # Show actual 10-bit stair steps, compressed to a bounded number of vertices.
                count = min(n, 1024)
                for k in range(1, count+1):
                    tick = max(1, round(k*n/count))
                    value = start + (1 if s.target >= start else -1) * (abs(s.target-start)*tick//n)
                    path.lineTo(*point(t + tick*duration/n, value))
                t += duration
                start = s.target
        fill = QPainterPath(path)
        fill.lineTo(area.right(), area.bottom()); fill.lineTo(area.left(), area.bottom()); fill.closeSubpath()
        gradient = QLinearGradient(0, area.top(), 0, area.bottom())
        gradient.setColorAt(0, QColor(37,99,235,48)); gradient.setColorAt(1, QColor(37,99,235,3))
        p.fillPath(fill, gradient)
        p.setPen(QPen(QColor("#2563eb"), 2.4)); p.drawPath(path)
        p.restore()
        if staged:
            from .functions import stage_counts
            HZ=self.caps.core_hz
            elapsed=0
            for name,count in zip(('进入','高电平保持（示意）' if gate else '保持','返回'),stage_counts(profile,self.caps.core_hz)):
                left=point(elapsed*1000/HZ,0)[0]
                elapsed+=count
                right=point(elapsed*1000/HZ,0)[0]
                if right-left>48:
                    p.setPen(QColor('#7186a3'))
                    p.drawText(QRectF(left,area.top()-24,right-left,18),Qt.AlignCenter,name)
                if 0<elapsed<c.duration_ms*HZ/1000:
                    p.setPen(QPen(QColor('#bed2f2'),1,Qt.DashLine))
                    p.drawLine(right,area.top(),right,area.bottom())
        if events:
            p.setPen(QColor("#7186a3"));p.drawText(QRectF(area.left(),area.top()-24,area.width(),20),Qt.AlignRight|Qt.AlignVCenter,"事件时间轴为示意，等待/高电平由输入决定")
        p.setPen(QColor("#647c9e")); p.drawText(8, 14, f"DAC 原码 · {self.caps.dac_bits} bit · 自动范围")
        p.drawText(self.width()-43, self.height()-8, axis_unit)
        if self.status:
            y = point(0, self.status["code"])[1]
            if area.top()<=y<=area.bottom():
                p.setPen(QPen(QColor("#d99742"), 1, Qt.DotLine))
                p.drawLine(area.left(), y, area.right(), y)
        p.end()
