"""Regression checks for draft loss and misleading execution controls."""
import copy
import json
import os
from dataclasses import replace

os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from PySide6.QtWidgets import QApplication,QMessageBox
from host.app import Studio
from host.storage import Library
from host.model import Profile,fingerprint
from tests.test_sequences import example


def test_step_selection_updates_editable_parameters_and_empty_selection(tmp_path):
    app=QApplication.instance() or QApplication([]);w=Studio(Library(tmp_path))
    try:
        p=example(events=True)
        p.sequence_steps[2].update(kind='delay',delay_ms=.002)
        page=w.sequence_page;page.load(p,False)
        page.table.selectRow(1)
        assert page.kind.currentData()=='gate' and page.step_source.isEnabled()
        assert not page.time.isEnabled() and not page.step_edge.isEnabled() and not page.step_repeat.isEnabled()
        page.table.selectRow(2)
        assert page.kind.currentData()=='delay' and page.time.isEnabled() and page.step_repeat.isEnabled()
        assert not page.step_source.isEnabled() and not page.step_edge.isEnabled()
        before=copy.deepcopy(page.steps[:2]);page.time.setValue(.003)
        assert page.steps[:2]==before and page.steps[2]['delay_ms']==.003
        page.table.selectRow(0)
        assert page.step_edge.isEnabled() and not page.time.isEnabled()
        while page.steps:page.remove()
        assert page.current_row==-1 and not page.time.isEnabled() and page.step_fields.isHidden()
        assert not page.save_button.isEnabled() and not page.update_button.isEnabled()
    finally:w.close();app.processEvents()


def test_sequence_open_preserves_same_draft_and_cancel_prevents_replacement(tmp_path,monkeypatch):
    app=QApplication.instance() or QApplication([]);library=Library(tmp_path)
    first=example();second=replace(example(),name='另一个序列')
    library.save(first);library.save(second);w=Studio(library)
    questions=[]
    def cancel(*args):questions.append(args);return QMessageBox.Cancel
    monkeypatch.setattr(QMessageBox,'question',cancel)
    try:
        page=w.sequence_page;page.load(first);page.name.setText('未保存的编排')
        draft=fingerprint(page.collect())
        w.open_dac(0);channel=w.channel_page;channel.plan.setCurrentIndex(channel.plan.findData(first.id))
        channel.edit_plan()
        assert not questions and fingerprint(page.collect())==draft
        w.open_dac(0);channel.plan.setCurrentIndex(channel.plan.findData(second.id));channel.edit_plan()
        assert len(questions)==1 and w.stack.currentIndex()==5 and fingerprint(page.collect())==draft
        page.library.setCurrentIndex(page.library.findData(second.id))
        assert page.library.currentData()==first.id and fingerprint(page.collect())==draft
        page.new();assert fingerprint(page.collect())==draft
        monkeypatch.setattr(QMessageBox,'question',lambda *args:QMessageBox.Save)
        assert page.open_profile(second)
        assert next(p for p in library.list() if p.id==first.id).name=='未保存的编排'
        assert page.profile.id==second.id
    finally:w.close();app.processEvents()


def test_timeline_software_trigger_and_events_have_distinct_controls(tmp_path):
    app=QApplication.instance() or QApplication([]);w=Studio(Library(tmp_path))
    try:
        page=w.sequence_page;page.load(example())
        page.trigger.setCurrentIndex(page.trigger.findData('software'))
        assert not page.source.isEnabled() and not page.edge.isEnabled()
        assert all(page.table.isColumnHidden(i) for i in (1,3,4))
        page.mode.setCurrentIndex(page.mode.findData('events'))
        assert page.source.isEnabled() and not page.trigger.isEnabled() and not page.repeat_mode.isEnabled()
        assert not any(page.table.isColumnHidden(i) for i in (1,3,4))
        page.mode.setCurrentIndex(page.mode.findData('timeline'))
        before=copy.deepcopy(page.steps);page.source.setCurrentIndex(1);page.common_input()
        assert page.steps==before
    finally:w.close();app.processEvents()


def test_flash_snapshot_does_not_report_save_success_after_invalid_board(tmp_path,monkeypatch):
    app=QApplication.instance() or QApplication([]);library=Library(tmp_path)
    plan=Profile(mode='ramp',name='上电测试');library.save(plan);w=Studio(library)
    errors=[];monkeypatch.setattr(QMessageBox,'warning',lambda *args:errors.append(args[-1]))
    try:
        w.parallel_page.rows[0]['combo'].setCurrentIndex(w.parallel_page.rows[0]['combo'].findData(plan.id))
        board=w.board_page;assert board.save();original=(tmp_path/'board.json').read_bytes()
        board.part.setText('invalid-part');board.flash_page.capture_boot()
        assert errors and '保存失败' in board.flash_page.feedback.text()
        assert (tmp_path/'board.json').read_bytes()==original
        assert board.process is None and board.flash_page.process is None
        board.flash_page.clear_boot();assert '保存失败' in board.flash_page.feedback.text()
    finally:w.close();app.processEvents()
