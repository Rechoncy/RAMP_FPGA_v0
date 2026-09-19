"""Small, nonblocking presentation effects; device commands are never animated."""
from PySide6.QtCore import Qt, QVariantAnimation, QPropertyAnimation, QEasingCurve, QRectF
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import (QPushButton, QStackedWidget, QLabel, QGraphicsOpacityEffect,
                              QWidget, QVBoxLayout, QGridLayout, QComboBox, QFrame)


class Foldout(QFrame):
    """A compact disclosure; hidden settings do not reserve page height."""
    def __init__(self,title):
        super().__init__();self.setObjectName('card');self.title=title
        box=QVBoxLayout(self);box.setContentsMargins(16,10,16,10);box.setSpacing(8)
        self.toggle=SoftButton('＋  '+title);self.toggle.setObjectName('disclosure');self.toggle.setCheckable(True);box.addWidget(self.toggle)
        self.content=QWidget();self.body=QVBoxLayout(self.content);self.body.setContentsMargins(4,4,4,8);self.body.setSpacing(12);box.addWidget(self.content);self.content.hide()
        self.toggle.toggled.connect(self.expand)

    def expand(self,value):
        self.content.setVisible(value);self.toggle.setText(('－  ' if value else '＋  ')+self.title)


def fit_combo(combo):
    """Keep long names in the popup instead of expanding the entire page."""
    combo.setMinimumContentsLength(8)
    combo.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
    combo.setMinimumWidth(0)
    return combo


class FieldGrid(QWidget):
    """Labelled inputs that wrap together when the available width changes."""
    def __init__(self, columns=3, minimum=190):
        super().__init__()
        self.columns=columns; self.minimum=minimum; self.fields=[]; self._key=None
        self.grid=QGridLayout(self); self.grid.setContentsMargins(0,0,0,0)
        self.grid.setHorizontalSpacing(12); self.grid.setVerticalSpacing(12)

    def add_field(self, title, control):
        field=QWidget(self); layout=QVBoxLayout(field)
        layout.setContentsMargins(0,0,0,0); layout.setSpacing(7)
        caption=title if isinstance(title,QLabel) else QLabel(title)
        caption.setObjectName('subtitle'); caption.setWordWrap(True)
        layout.addWidget(caption); layout.addWidget(control)
        if isinstance(control,QComboBox):fit_combo(control)
        self.fields.append((control,field)); field.setVisible(True); self.arrange()
        return field

    def show_field(self, control, visible):
        next(field for widget,field in self.fields if widget is control).setVisible(visible)
        self.arrange()

    def arrange(self):
        visible=[field for _,field in self.fields if not field.isHidden()]
        columns=max(1,min(self.columns,max(1,len(visible)),(self.width()+12)//(self.minimum+12)))
        key=(columns,tuple(visible))
        if key==self._key:return
        self._key=key
        while self.grid.count():self.grid.takeAt(0)
        for column in range(self.columns):self.grid.setColumnStretch(column,1 if column<columns else 0)
        for i,field in enumerate(visible):self.grid.addWidget(field,i//columns,i%columns)
        self.updateGeometry()

    def resizeEvent(self,event):
        self.arrange();super().resizeEvent(event)


def blend(a,b,t):
    a,b=QColor(a),QColor(b)
    return QColor.fromRgbF(*(x+(y-x)*t for x,y in zip(a.getRgbF(),b.getRgbF())))


class SoftButton(QPushButton):
    """Rounded buttons with a short color interpolation on pointer movement."""
    def __init__(self,*args,**kwargs):
        super().__init__(*args,**kwargs)
        self.setCursor(Qt.PointingHandCursor)
        self._hover=0.0
        self._motion=QVariantAnimation(self)
        self._motion.setDuration(150)
        self._motion.setEasingCurve(QEasingCurve.OutCubic)
        self._motion.valueChanged.connect(self._frame)

    def _frame(self,value):
        self._hover=float(value); self.update()

    def _animate(self,target):
        self._motion.stop(); self._motion.setStartValue(self._hover)
        self._motion.setEndValue(target); self._motion.start()

    def enterEvent(self,event):
        self._animate(1.0); super().enterEvent(event)

    def leaveEvent(self,event):
        self._animate(0.0); super().leaveEvent(event)

    def paintEvent(self,event):
        role=self.objectName()
        if role=='nav':
            bg,over,fg,line=('#dfebff','#d2e3ff','#1d4ed8','#dfebff') if self.isChecked() else ('#00ffffff','#ecf3ff','#536e94','#00ffffff')
        elif role=='disclosure':bg,over,fg,line='#00ffffff','#f2f6fd','#355784','#00ffffff'
        elif role=='primary': bg,over,fg,line='#2563eb','#1d4ed8','#ffffff','#2563eb'
        elif role=='danger': bg,over,fg,line='#fff5f2','#fbe7e1','#b3594b','#efdcd5'
        else: bg,over,fg,line='#ffffff','#edf4ff','#355784','#dce7f7'
        if not self.isEnabled():
            bg=over='#00ffffff' if role=='nav' else '#f3f6fb'; fg='#94a4bd'; line='#00ffffff' if role=='nav' else '#e9eff8'
        color=blend(bg,over,self._hover)
        if self.isDown(): color=color.darker(105)
        painter=QPainter(self); painter.setRenderHint(QPainter.Antialiasing)
        bounds=QRectF(self.rect()).adjusted(.75,.75,-.75,-.75)
        painter.setBrush(color)
        painter.setPen(QPen(QColor('#6195ee') if self.hasFocus() else QColor(line),1.2 if self.hasFocus() else 1))
        radius=min(13,bounds.height()/2)
        painter.drawRoundedRect(bounds,radius,radius)
        if role=='nav' and self.isChecked():
            painter.setPen(Qt.NoPen); painter.setBrush(QColor('#2563eb'))
            painter.drawRoundedRect(QRectF(5,12,3,max(8,self.height()-24)),1.5,1.5)
        painter.setPen(QColor(fg)); painter.setFont(self.font())
        alignment=Qt.AlignVCenter | (Qt.AlignLeft if role in ('nav','disclosure') else Qt.AlignHCenter)
        painter.drawText(bounds.adjusted(15,0,-15,0),alignment | Qt.TextShowMnemonic,self.text())


class SoftStack(QStackedWidget):
    """Switch immediately; fade only the old visual snapshot for 180 ms."""
    def __init__(self,*args):
        super().__init__(*args)
        self._overlay=None; self._fade=None

    def _clear_transition(self):
        if self._fade:
            self._fade.stop(); self._fade.deleteLater(); self._fade=None
        if self._overlay:
            self._overlay.hide(); self._overlay.deleteLater(); self._overlay=None

    def setCurrentIndex(self,index):
        if index==self.currentIndex(): return
        self._clear_transition()
        snapshot=self.grab() if self.isVisible() and self.currentWidget() else None
        super().setCurrentIndex(index)
        if snapshot is None or snapshot.isNull(): return
        self._overlay=QLabel(self); self._overlay.setPixmap(snapshot)
        self._overlay.setGeometry(self.rect())
        self._overlay.setAttribute(Qt.WA_TransparentForMouseEvents)
        effect=QGraphicsOpacityEffect(self._overlay); self._overlay.setGraphicsEffect(effect)
        self._overlay.show(); self._overlay.raise_()
        self._fade=QPropertyAnimation(effect,b'opacity',self)
        self._fade.setDuration(180); self._fade.setStartValue(1.0); self._fade.setEndValue(0.0)
        self._fade.setEasingCurve(QEasingCurve.OutCubic)
        self._fade.finished.connect(self._clear_transition); self._fade.start()

    def resizeEvent(self,event):
        self._clear_transition(); super().resizeEvent(event)
