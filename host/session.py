"""Local exit snapshots restore editable settings, never device commands/state."""
import copy
import hashlib
import json
import os
from dataclasses import fields
from pathlib import Path
from PySide6.QtCore import QByteArray,Qt
from PySide6.QtWidgets import QAbstractSpinBox,QApplication
from .model import Profile,Segment,fingerprint


def atomic_json(path,data):
    path=Path(path);temp=path.with_suffix('.tmp')
    payload=json.dumps(data,ensure_ascii=False,indent=2,allow_nan=False)
    with temp.open('w',encoding='utf-8') as stream:
        stream.write(payload);stream.flush();os.fsync(stream.fileno())
    temp.replace(path)


def draft_profile(data):
    """Drafts may contain invalid expressions or incomplete names."""
    if not isinstance(data,dict):raise ValueError('草稿格式不正确')
    known={f.name for f in fields(Profile)}
    values={key:copy.deepcopy(value) for key,value in data.items() if key in known}
    values['segments']=[Segment(**v) for v in values.get('segments',[])]
    return Profile(**values)


class WorkspaceSession:
    def __init__(self,root):
        self.root=Path(root);self.path=self.root/'desktop_session.json';self.data={};self.error=''
        try:
            if self.path.exists():
                data=json.loads(self.path.read_text(encoding='utf-8'))
                if not isinstance(data,dict) or data.get('schema')!=1:raise ValueError('不支持的设置快照版本')
                self.data=data
        except (OSError,ValueError,TypeError) as exc:self.error=f'退出设置未恢复：{exc}'

    @staticmethod
    def commit_editors(studio):
        incomplete=[(field,field.lineEdit().text()) for field in studio.findChildren(QAbstractSpinBox) if not field.hasAcceptableInput()]
        focus=QApplication.focusWidget()
        if focus and studio.isAncestorOf(focus):focus.clearFocus()
        # interpretText emits valueChanged for TimeInput's canonical duration.
        for field in studio.findChildren(QAbstractSpinBox):
            if field.hasAcceptableInput():field.interpretText()
        # Focus-out corrects intermediate numeric input. Preserve it as a draft.
        for field,text in incomplete:field.lineEdit().setText(text)

    def capture(self,s):
        seq=s.sequence_page;scan=s.scan_page;cp=s.channel_page
        return dict(schema=1,board=s.board_page.capture_session(),
            designer=s.capture_dac_draft(),
            sequence=dict(profile=seq.collect().to_dict(),saved=seq.saved,finite_repeat=seq.repeat.value(),
                          loaded=seq.loaded_fingerprint,row=seq.current_row,time_unit=seq.time.unit.currentData(),wave_id=seq.wave.currentData()),
            scan=dict(profile=scan.collect().to_dict(),saved=scan.saved,fresh=scan.fresh and fingerprint(scan.collect())==scan.loaded),
            assignments=copy.deepcopy(s.parallel_page.store.assignments),
            debug=dict(channel=s.debug_page.channel.currentData(),code=s.debug_page.code.value(),safe=s.debug_page.safe.value()),
            view=dict(page=s.stack.currentIndex(),designer_tab=s.designer_tabs.currentIndex(),board_tab=s.board_page.tabs.currentIndex(),
                      active_dac=s.active_dac,port=s.ports.currentData(),baudrate=s.baudrate.currentData(),search=s.search.text(),
                      connection_expanded=not s.connection_controls.isHidden(),library_expanded=s.library_toggle.isChecked(),
                      channel_view=cp.view.currentData(),channel_settings=cp.settings_section.toggle.isChecked(),
                      channel_versions=cp.version_section.toggle.isChecked(),resource_details=s.board_page.utilization_panel.details.toggle.isChecked(),
                      geometry=bytes(s.saveGeometry().toBase64()).decode('ascii'),maximized=s.isMaximized()))

    def save(self,s):
        self.commit_editors(s)
        data=self.capture(s)
        board=s.board_page.valid_settings()
        if board is not None:
            atomic_json(self.root/'board.json',board.to_dict())
            s.board_page.board=board
        s.stash_dac_draft(strict=True)
        atomic_json(self.root/'sequence_draft.json',data['sequence']['profile'])
        atomic_json(self.root/'scan_draft.json',data['scan'])
        atomic_json(self.root/'parallel.json',data['assignments'])
        s.settings.setValue('geometry',s.saveGeometry());s.settings.sync()
        if s.settings.status()!=s.settings.Status.NoError:raise OSError('窗口设置无法写入，请检查数据目录权限和磁盘空间')
        if self.error and self.path.exists():
            raw=self.path.read_bytes();folder=self.root/'archive'/'settings_recovery';folder.mkdir(parents=True,exist_ok=True)
            backup=folder/(hashlib.sha256(raw).hexdigest()+'.json')
            if not backup.exists():backup.write_bytes(raw)
        atomic_json(self.path,data)
        self.data=data;self.error=''

    def restore_board(self,s):
        if self.data.get('board'):
            try:
                if not isinstance(self.data['board'],dict) or not s.board_page.restore_session(self.data['board']):
                    raise ValueError('板卡草稿格式或版本不受支持')
            except (ValueError,TypeError,KeyError,AttributeError,OverflowError) as exc:self.error=f'板卡编辑状态未恢复：{exc}'

    def restore_editors(self,s):
        warnings=[]
        d=self.data.get('designer')
        if d:
            try:s.restore_dac_draft(d['profile'].get('channel',0),d)
            except (ValueError,TypeError,KeyError,AttributeError,OverflowError) as exc:warnings.append(f'波形草稿：{exc}')
        d=self.data.get('sequence')
        if d:
            try:
                p=draft_profile(d['profile']);seq=s.sequence_page;seq.load(p,saved=False)
                seq.loading=True;seq.saved=d.get('saved','');seq.loaded_fingerprint=d.get('loaded',seq.loaded_fingerprint);seq.repeat.setValue(d.get('finite_repeat',p.repeat or 1))
                seq.time.setUnit(d.get('time_unit','ms'),preserve_duration=True)
                seq.wave.setCurrentIndex(max(0,seq.wave.findData(d.get('wave_id'))));seq.current_row=d.get('row',-1)
                seq.loading=False;seq.edited()
            except (ValueError,TypeError,KeyError,AttributeError,OverflowError) as exc:
                s.sequence_page.loading=False;warnings.append(f'序列草稿：{exc}')
        d=self.data.get('scan')
        if d:
            try:s.scan_page.load(draft_profile(d['profile']),saved=False,saved_fingerprint=d.get('saved',''),fresh=d.get('fresh',False))
            except (ValueError,TypeError,KeyError,AttributeError,OverflowError) as exc:warnings.append(f'扫描草稿：{exc}')
        assignments=self.data.get('assignments')
        if isinstance(assignments,dict):
            previous=s.parallel_page.store.assignments
            try:
                if any(k not in {str(i) for i in range(8)} or not isinstance(v,dict) or not isinstance(v.get('trigger_override',{}),dict) for k,v in assignments.items()):
                    raise ValueError('DAC 分配格式不正确')
                s.parallel_page.store.assignments=copy.deepcopy(assignments);s.parallel_page.saved=s.parallel_page.store.assignments
                s.parallel_page.rebuild(s.caps.dac_count,s.caps.dac_bits)
            except (ValueError,TypeError,KeyError,AttributeError,OverflowError) as exc:
                s.parallel_page.store.assignments=previous;s.parallel_page.saved=previous
                s.parallel_page.rebuild(s.caps.dac_count,s.caps.dac_bits)
                warnings.append(f'DAC 分配：{exc}')
        if warnings:self.error='；'.join(filter(None,[self.error,'部分退出设置未恢复：'+'；'.join(warnings)]))

    def restore_view(self,s):
        if not self.data:
            if self.error:s.notice(self.error)
            return
        d=self.data.get('view',{})
        try:
            if not isinstance(d,dict):raise ValueError('界面设置格式不正确')
            if d.get('geometry'):s.restoreGeometry(QByteArray.fromBase64(d['geometry'].encode('ascii')))
            if d.get('maximized'):s.setWindowState(s.windowState()|Qt.WindowMaximized)
            port=d.get('port')
            if isinstance(port,str):
                if s.ports.findData(port)<0:s.ports.addItem(f'{port} · 当前未找到',port)
                s.ports.setCurrentIndex(s.ports.findData(port))
            baud=d.get('baudrate',115200)
            if type(baud) is int and s.baudrate.findData(baud)>=0:s.baudrate.setCurrentIndex(s.baudrate.findData(baud))
            s.search.setText(d.get('search',''));s.library_toggle.setChecked(d.get('library_expanded',False));s.arrange_actions()
            s.designer_tabs.setCurrentIndex(max(0,min(2,d.get('designer_tab',0))))
            s.board_page.tabs.setCurrentIndex(max(0,min(s.board_page.tabs.count()-1,d.get('board_tab',0))))
            channel=max(0,min(s.caps.dac_count-1,d.get('active_dac',0)))
            s.active_dac=channel;s.channel_page.set_channel(channel)
            s.channel_page.view.setCurrentIndex(max(0,s.channel_page.view.findData(d.get('channel_view','loaded'))))
            s.channel_page.settings_section.toggle.setChecked(d.get('channel_settings',False))
            s.channel_page.version_section.toggle.setChecked(d.get('channel_versions',False))
            s.board_page.utilization_panel.details.toggle.setChecked(d.get('resource_details',False))
            s.navigate(max(0,min(5,d.get('page',0))))
            expanded=d.get('connection_expanded',s.board_page.tabs.currentWidget() is s.board_page.info_page)
            s.connection_controls.setVisible(expanded);s.connection_toggle.setText('收起连接设置' if expanded else '连接设置')
            debug=self.data.get('debug',{});page=s.debug_page
            channel=debug.get('channel',0)
            if isinstance(channel,int) and 0<=channel<8:
                if page.channel.findData(channel)<0:page.channel.addItem(f'DAC {channel+1} · 当前不可用',channel)
                page.channel.setCurrentIndex(page.channel.findData(channel))
            for key in ('code','safe'):
                value=debug.get(key)
                if type(value) is int and 0<=value<=16383:
                    field=getattr(page,key);field.setMaximum(max(field.maximum(),value));field.setValue(value)
            page.refresh()
        except (ValueError,TypeError,KeyError,AttributeError,OverflowError) as exc:self.error=f'界面状态未完整恢复：{exc}'
        if self.error:s.notice(self.error)
        elif self.data:s.notice('已恢复上次退出的设置与草稿 · 设备尚未连接')
