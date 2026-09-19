"""Separate channel navigation from the persistent scheme designer draft."""
import json,uuid,hashlib
from .model import Profile,fingerprint
from .appearance import SoftButton
from .protocol import STATES


class DacNavigation:
    draft_values=('initial','safe','update_us','repeat','amplitude','rise_ms','hold_ms','fall_ms','function_ms','interpolation_error')
    draft_texts=('name','function','rise_function','fall_function')
    draft_combos=('mode','trigger','trigger_input','trigger_edge','timing_mode','function_layout','repeat_mode','design_bits','playback')

    def refresh_dac_navigation(self):
        if not hasattr(self,'board_page'):return
        count=max(4,self.caps.dac_count)
        if len(self.dac_buttons)!=count:
            for b in self.dac_buttons:
                b.hide();b.setParent(None);b.deleteLater()
            while self.dac_nav_layout.count():self.dac_nav_layout.takeAt(0)
            self.dac_buttons=[]
            for i in range(count):
                b=SoftButton();b.setObjectName('nav');b.setCheckable(True);b.setFixedHeight(62)
                b.clicked.connect(lambda checked=False,ch=i:self.open_dac(ch))
                self.dac_nav_layout.addWidget(b);self.dac_buttons.append(b)
        selected=self.channel.currentData()
        compact=self.height()<820
        self.sidebar_footer.setText(f'{self.caps.core_hz/1e6:g} MSPS · {1e9/self.caps.core_hz:g} ns / 点' if compact else f'{self.caps.core_hz/1e6:g} MSPS\nFPGA waveform control')
        for i,b in enumerate(self.dac_buttons):
            available=i<self.caps.dac_count
            status=self.channel_statuses.get(i)
            if not self.caps.parallel_channels and self.last_status and self.last_status.get('channel')==i:status=self.last_status
            detail=f'{STATES.get(status["state"],"未知")} · {status["code"]}' if self.connected and status else '选择已保存方案' if available else '当前未配置'
            b.setFixedHeight(34 if compact else 40)
            b.setText(f'    DAC {i+1}');b.setToolTip(f'DAC {i+1} · {detail}');b.setEnabled(available and not self.busy)
            b.setChecked(self.stack.currentIndex()==5 and self.active_dac==i)
        if selected is not None:
            self.channel_badge.setText(f'DAC {selected+1}')
        for index,b in self.nav_buttons.items():
            b.setFixedHeight(38 if compact else 48);b.setChecked(self.stack.currentIndex()==index)

    def capture_dac_draft(self):
        if self.legacy_readonly:return None
        ch=self.channel.currentData()
        if ch is None:return None
        draft={'profile':self.profile.to_dict(),'saved_fingerprint':self.saved_fingerprint,
               'values':{key:getattr(self,key).value() for key in self.draft_values},
               'texts':{key:getattr(self,key).text() for key in self.draft_texts},
               'combos':{key:getattr(self,key).currentData() for key in self.draft_combos},
               'units':{key:getattr(self,key).unit.currentData() for key in ('rise_ms','hold_ms','fall_ms','function_ms','update_us')},
               'table':[[self.table.item(r,c).text() if self.table.item(r,c) else '' for c in range(4)] for r in range(self.table.rowCount())]}
        draft['profile']['channel']=ch
        return draft

    def stash_dac_draft(self,*,strict=False):
        draft=self.capture_dac_draft()
        if draft is None:return
        ch=draft['profile']['channel']
        self.dac_drafts[str(ch)]=draft
        try:
            path=self.library.root/'designer_draft.json';temp=path.with_suffix('.tmp')
            if path.exists():
                from .legacy_profile import is_legacy_draft
                raw=path.read_bytes()
                try:old=json.loads(raw)
                except (ValueError,TypeError):old={}
                drafts=old.get('drafts',{}) if isinstance(old,dict) else {}
                if isinstance(drafts,dict) and any(is_legacy_draft(d) for d in drafts.values()):
                    backup=self.library.root/'archive'/f'legacy_designer_draft_{hashlib.sha256(raw).hexdigest()[:16]}.json'
                    backup.parent.mkdir(exist_ok=True)
                    if not backup.exists():backup.write_bytes(raw)
            temp.write_text(json.dumps({'selected':ch,'drafts':self.dac_drafts},ensure_ascii=False,indent=2),encoding='utf-8');temp.replace(path)
        except OSError as exc:
            if strict:raise
            self.notice(f'草稿保留在当前窗口，写入文件失败：{exc}')

    def restore_dac_draft(self,ch,draft):
        from .legacy_profile import is_legacy_draft,draft_snapshot
        if is_legacy_draft(draft):
            self.load_profile(draft_snapshot(draft));return
        from PySide6.QtWidgets import QTableWidgetItem
        p=Profile.from_dict(draft['profile']);p.channel=ch
        self.load_profile(p);self.loading=True
        try:
            for key,value in draft.get('values',{}).items():
                if key in self.draft_values:
                    field=getattr(self,key)
                    # Retain out-of-range DAC codes for validation after a width change.
                    if key in ('initial','safe','amplitude'):field.setMaximum(max(field.maximum(),value))
                    field.setValue(value)
            for key,value in draft.get('texts',{}).items():
                if key in self.draft_texts:getattr(self,key).setText(value)
            for key,value in draft.get('combos',{}).items():
                if key in self.draft_combos:
                    field=getattr(self,key);index=field.findData(value)
                    if index<0 and key=='trigger_input':field.addItem(f'输入 {value+1} · 当前不可用',value);index=field.findData(value)
                    field.setCurrentIndex(index)
            for key,value in draft.get('units',{}).items():
                if key in ('rise_ms','hold_ms','fall_ms','function_ms','update_us'):getattr(self,key).setUnit(value,preserve_duration=True)
            if 'table' in draft:
                self.table.setRowCount(len(draft['table']))
                for r,row in enumerate(draft['table']):
                    for c,text in enumerate(row):self.table.setItem(r,c,QTableWidgetItem(text))
            saved=draft.get('saved_fingerprint','')
            try:self.saved_fingerprint=fingerprint(Profile.from_dict(json.loads(saved))) if saved else ''
            except (ValueError,TypeError,KeyError):self.saved_fingerprint=''
        finally:self.loading=False
        self.edited()

    def open_dac(self,ch):
        if self.busy or not 0<=ch<self.caps.dac_count:return
        self.active_dac=ch
        self.channel_page.set_channel(ch)
        self.navigate(5)
        self.debug_page.channel.setCurrentIndex(self.debug_page.channel.findData(ch))

    def restore_dac_session(self):
        path=self.library.root/'designer_draft.json'
        if not path.exists():path=self.library.root/'dac_editors.json'
        if not path.exists():return
        try:
            data=json.loads(path.read_text(encoding='utf-8'));drafts=data['drafts']
            if not isinstance(drafts,dict):raise ValueError('Invalid drafts')
            self.dac_drafts={key:value for key,value in drafts.items() if key in [str(i) for i in range(8)]}
            ch=data.get('selected',0)
            if isinstance(ch,int) and 0<=ch<8 and str(ch) in self.dac_drafts:
                from .legacy_profile import is_legacy_draft
                if is_legacy_draft(self.dac_drafts[str(ch)]):self.notice('已保留历史分段草稿，启动使用当前波形编辑器；历史方案可在方案库查看。')
                else:self.restore_dac_draft(ch,self.dac_drafts[str(ch)])
        except (ValueError,TypeError,KeyError,OSError) as exc:self.notice(f'设计草稿未恢复：{exc}')

    def recover_legacy_draft(self):
        from PySide6.QtWidgets import QInputDialog
        from .legacy_profile import recoverable_drafts
        try:
            options=recoverable_drafts(self.library.root);labels=[title for title,_ in options]
            if not labels:return
            chosen,ok=QInputDialog.getItem(self,'恢复历史草稿','选择草稿；历史分段将以只读方式打开，可转换为 Ramp 副本：',labels,0,False)
            if not ok or not self.maybe_discard():return
            self.restore_dac_draft(0,options[labels.index(chosen)][1])
            if self.legacy_readonly:
                self.notice('已打开历史草稿只读预览；可转换为 Ramp 副本，原草稿文件保留。');return
            self.profile.id=uuid.uuid4().hex;self.saved_fingerprint='';self.name.setText(self.name.text()+' · 恢复副本');self.edited()
            self.notice('旧版草稿已恢复为设计副本，请检查并保存；原始草稿文件保留。')
        except (ValueError,TypeError,KeyError,OSError) as exc:self.notice(f'旧版草稿无法恢复：{exc}')
