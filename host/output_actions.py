"""One compact action strip with an explicit, caller-owned channel scope."""
from PySide6.QtWidgets import QFrame,QVBoxLayout,QHBoxLayout,QLabel,QMenu
from .appearance import SoftButton


class OutputActions(QFrame):
    def __init__(self,callback,abort_all):
        super().__init__();self.setObjectName('card')
        layout=QVBoxLayout(self);layout.setContentsMargins(16,12,16,12);layout.setSpacing(8)
        row=QHBoxLayout();self.scope=QLabel();self.scope.setObjectName('section');row.addWidget(self.scope);row.addStretch()
        self.buttons={}
        for key,text in [('prepare','准备所选'),('trigger','软件触发'),('abort','中止所选'),('more','更多操作')]:
            b=SoftButton(text);self.buttons[key]=b;row.addWidget(b)
            if key!='more':b.clicked.connect(lambda checked=False,k=key:callback(k))
        self.buttons['prepare'].setObjectName('primary');self.buttons['abort'].setObjectName('danger');layout.addLayout(row)
        self.hint=QLabel();self.hint.setWordWrap(True);self.hint.setObjectName('subtitle');layout.addWidget(self.hint)
        self.feedback=QLabel();self.feedback.setWordWrap(True);layout.addWidget(self.feedback);self.feedback.hide()
        self.menu=QMenu(self);self.manual={}
        for key,text in [('upload','仅下载方案'),('arm','仅布防'),('disarm','取消布防'),('external','模拟触发'),('falling','模拟下降沿')]:
            b=SoftButton(text,self);b.hide();self.buttons[key]=b
            b.clicked.connect(lambda checked=False,k=key:callback(k))
            action=self.menu.addAction(text);action.triggered.connect(b.click);self.manual[key]=action
        self.menu.addSeparator();self.all_action=self.menu.addAction('中止全部通道');self.all_action.triggered.connect(abort_all)
        self.buttons['more'].clicked.connect(lambda:self.menu.exec(self.buttons['more'].mapToGlobal(self.buttons['more'].rect().bottomLeft())))
        self.menu.aboutToShow.connect(self.sync_menu)

    def sync_menu(self):
        for key,action in self.manual.items():
            button=self.buttons[key];action.setText(button.text());action.setEnabled(button.isEnabled())
            action.setVisible(button.property('available') is not False)

    def message(self,text):self.feedback.setText(text);self.feedback.setVisible(bool(text))
