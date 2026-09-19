"""Bounded, cached previews of confirmed samples and a separate local revision."""
from dataclasses import replace
from PySide6.QtCore import Qt,QRectF,Signal
from PySide6.QtGui import QPainter,QPainterPath,QColor,QPen,QLinearGradient
from PySide6.QtWidgets import QWidget
from .model import Capabilities,compile_profile,fingerprint
from .plot_scale import code_axis
from .time_input import display_time


def curve(profile,caps):
    if profile.mode=='scan':
        from .scan import preview_scan
        compile_profile(profile,replace(caps,trigger_scan=True))
        return preview_scan(profile)
    import numpy as np
    from .functions import preview_samples
    from .sequences import preview_sequence
    compile_profile(profile,caps)
    t,_,codes,total=preview_sequence(profile,caps.core_hz) if profile.mode=='sequence' else preview_samples(profile,caps.core_hz)
    if len(t)>3200:
        indices={0,len(t)-1}
        for start,end in zip(np.linspace(0,len(t),801,dtype=int)[:-1],np.linspace(0,len(t),801,dtype=int)[1:]):
            section=codes[start:end]
            indices.update((start,end-1,start+int(np.argmin(section)),start+int(np.argmax(section))))
        indices=sorted(indices);t=t[indices];codes=codes[indices]
    times=list(map(float,t));values=list(map(int,codes))
    # Infinite playback starts the next cycle immediately, without an idle code.
    times.extend([total,total]);values.extend([values[-1],values[0] if profile.repeat==0 else profile.initial])
    schematic=profile.timing_mode=='gate' or (profile.mode=='sequence' and profile.sequence_mode=='events')
    return times,values,total,schematic


class OutputWaveformPlot(QWidget):
    clicked=Signal()
    def __init__(self,thumbnail=False):
        super().__init__();self.thumbnail=thumbnail;self.caps=Capabilities();self.status=None
        self.profile=None;self.local=None;self.mode='loaded';self._key=None;self.curves=[];self.error='';self.y_axis=None
        self.empty='尚未确认已加载波形'
        self.setMinimumHeight(54 if thumbnail else 250)
        if thumbnail:
            self.setCursor(Qt.PointingHandCursor);self.setFixedWidth(180);self.setFixedHeight(64)
            self.setToolTip('波形按当前数值范围自动缩放。不同缩略图独立缩放，请查看 DAC 原码比较绝对幅度，不能直接比较曲线高度。')

    def set_profiles(self,loaded,local,caps,mode='loaded',empty='尚未确认已加载波形'):
        self.profile=loaded;self.local=local;self.caps=caps;self.mode=mode;self.empty=empty
        key=(fingerprint(loaded) if loaded else None,fingerprint(local) if local else None,caps,mode,empty)
        if key==self._key:return
        self._key=key;self.curves=[];self.error='';self.y_axis=None
        for p,dashed in ((loaded,False),(local,True)):
            if p is None or (mode=='loaded' and dashed) or (mode=='local' and not dashed):continue
            try:self.curves.append((curve(p,replace(caps,infinite_repeat=True,interpolation=True,dac_bits=p.dac_bits) if dashed else caps),dashed))
            except (ValueError,ArithmeticError) as exc:self.error=str(exc)
        basis=lambda p:'scan' if p.mode=='scan' else 'gate' if p.timing_mode=='gate' else 'events' if p.mode=='sequence' and p.sequence_mode=='events' else 'fixed'
        if len(self.curves)==2 and basis(loaded)!=basis(local):
            self.curves=self.curves[:1];self.error='横轴类型不同，请切换查看本地方案'
        if self.curves:
            self.y_axis=code_axis([value for (data,_) in self.curves for value in data[1]],caps.dac_bits)
        if self.thumbnail:
            span=f'当前显示范围 {self.y_axis.minimum}～{self.y_axis.maximum} 原码。' if self.y_axis else '暂无波形。'
            self.setToolTip(span+'不同缩略图独立缩放，请查看 DAC 原码比较绝对幅度，不能直接比较曲线高度。')
        self.update()

    def mouseReleaseEvent(self,event):
        if event.button()==Qt.LeftButton:self.clicked.emit()
        super().mouseReleaseEvent(event)

    def paintEvent(self,event):
        painter=QPainter(self);painter.setRenderHint(QPainter.Antialiasing)
        area=QRectF(5,6,self.width()-10,self.height()-12) if self.thumbnail else QRectF(56,30,self.width()-80,self.height()-74)
        if not self.curves:
            painter.setPen(QColor('#7186a3'))
            text='暂无波形' if self.thumbnail else self.error or self.empty
            painter.drawText(area,Qt.AlignCenter|Qt.TextWordWrap,text);return
        total=max(c[0][2] for c in self.curves);axis=self.y_axis
        schematic=any(c[0][3] for c in self.curves)
        scan=all(c[0][3]=='scan' for c in self.curves)
        def xy(t,value):return area.left()+float(t)/total*area.width(),area.bottom()-(value-axis.minimum)/axis.span*area.height()
        if not self.thumbnail:
            axis_total,unit=display_time(total)
            for value in axis.ticks:
                y=xy(0,value)[1]
                painter.setPen(QPen(QColor('#e9eff9'),1));painter.drawLine(area.left(),y,area.right(),y)
                painter.setPen(QColor('#7186a3'));painter.drawText(QRectF(0,y-10,46,20),Qt.AlignRight|Qt.AlignVCenter,str(int(value)))
            if scan:
                import math
                count=int(total);step=max(1,math.ceil(count/5))
                for index in sorted({0,count,*range(0,count+1,step)}):
                    x=xy(index,0)[0]
                    painter.setPen(QPen(QColor('#e9eff9'),1));painter.drawLine(x,area.top(),x,area.bottom())
                    painter.setPen(QColor('#7186a3'));painter.drawText(QRectF(x-35,area.bottom()+7,70,20),Qt.AlignCenter,str(index))
                painter.drawText(QRectF(0,self.height()-18,self.width()-12,18),Qt.AlignRight,'触发次数 · 0 = 等待首次触发')
            elif not schematic:
                for i in range(6):
                    x=area.left()+i*area.width()/5
                    painter.setPen(QPen(QColor('#e9eff9'),1));painter.drawLine(x,area.top(),x,area.bottom())
                    painter.setPen(QColor('#7186a3'));painter.drawText(QRectF(x-35,area.bottom()+7,70,20),Qt.AlignCenter,f'{axis_total*i/5:.3g}')
                painter.drawText(QRectF(0,self.height()-18,self.width()-12,18),Qt.AlignRight,unit)
            else:
                painter.setPen(QColor('#7186a3'))
                painter.drawText(QRectF(area.left(),area.bottom()+8,area.width(),30),Qt.AlignCenter,'事件顺序示意 · 等待 / 高电平保持的时长由外部输入决定')
            from .sequences import has_interpolation
            reference=any(p is not None and has_interpolation(p) for p in (self.profile,self.local))
            painter.setPen(QColor('#7186a3'));painter.drawText(4,17,f'DAC 原码 · {self.caps.dac_bits} bit · 自动范围'+(' · 原函数参考，插值差异见完整检查' if reference else ''))
            if self.error:painter.drawText(QRectF(0,0,self.width(),24),Qt.AlignRight,self.error)
        painter.save();painter.setClipRect(area.adjusted(-2,-2,2,2))
        for (times,values,_,_),dashed in self.curves:
            path=QPainterPath();path.moveTo(*xy(times[0],values[0]))
            for t,v in zip(times[1:],values[1:]):path.lineTo(*xy(t,v))
            if not dashed:
                fill=QPainterPath(path);fill.lineTo(*xy(times[-1],axis.minimum));fill.lineTo(*xy(times[0],axis.minimum));fill.closeSubpath()
                gradient=QLinearGradient(0,area.top(),0,area.bottom());gradient.setColorAt(0,QColor(37,99,235,40));gradient.setColorAt(1,QColor(37,99,235,3));painter.fillPath(fill,gradient)
            painter.setPen(QPen(QColor('#8499b8' if dashed else '#2563eb'),1.8 if dashed else 2.3,Qt.DashLine if dashed else Qt.SolidLine));painter.drawPath(path)
        painter.restore()
