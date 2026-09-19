"""User-selected global configuration archives and offline workspace reload."""
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QProcess, Qt
from PySide6.QtWidgets import QFileDialog, QMessageBox

from .workspace_archive import read_archive, save_archive, restore_archive


class WorkspaceTransfer:
    def workspace_transfer_blocked(self, importing=False):
        board = self.board_page
        if self.busy or self._closing_disconnect or getattr(self, '_workspace_reopen', None):
            return '请等待当前操作结束。'
        if board.flash_page.running():
            return '请等待 Flash 操作结束。'
        if board.process and board.process.state() != QProcess.NotRunning:
            return '请等待 Vivado 构建结束。'
        if importing and self.connected:
            return '全局导入会替换工作区配置，请先断开设备连接。'
        return ''

    def save_workspace(self, path):
        """Save current drafts as well as library versions; never send commands."""
        blocked = self.workspace_transfer_blocked()
        if blocked:
            raise ValueError(blocked)
        self.session.save(self)
        summary = save_archive(self.library.root, path)
        self.notice(f'全局保存完成 · {summary["profile_count"]} 个方案 · {path}')
        return summary

    def choose_save_workspace(self):
        try:
            blocked = self.workspace_transfer_blocked()
            if blocked:
                raise ValueError(blocked)
            path, _ = QFileDialog.getSaveFileName(
                self, '全局保存 · 配置与方案',
                f'Ramp-Studio-{datetime.now():%Y%m%d-%H%M%S}.ramp-workspace',
                'Ramp Studio 全局配置包 (*.ramp-workspace)')
            if not path:
                return
            if not Path(path).suffix:
                path += '.ramp-workspace'
            self.save_workspace(path)
        except (OSError, ValueError, TypeError) as exc:
            QMessageBox.warning(self, '全局保存未完成', str(exc))

    def import_workspace(self, snapshot):
        """Install validated data, then close without resaving the old widgets."""
        blocked = self.workspace_transfer_blocked(importing=True)
        if blocked:
            raise ValueError(blocked)
        self.session.save(self)
        backup = restore_archive(self.library.root, snapshot)
        self._workspace_reopen = (str(self.library.root), str(backup))
        self._exit_saved = True
        self.centralWidget().setEnabled(False)
        self.resource_dialog.hide()
        self.notice('全局配置已导入，正在重新打开工作区…')
        self.close()
        return backup

    def choose_import_workspace(self):
        try:
            blocked = self.workspace_transfer_blocked(importing=True)
            if blocked:
                raise ValueError(blocked)
            path, _ = QFileDialog.getOpenFileName(
                self, '全局导入 · 配置与方案', '',
                'Ramp Studio 全局配置包 (*.ramp-workspace)')
            if not path:
                return
            snapshot = read_archive(path)
            info = snapshot.summary
            message = QMessageBox(self)
            message.setWindowTitle('导入全局配置')
            message.setIcon(QMessageBox.Question)
            message.setTextFormat(Qt.PlainText)
            message.setText(
                f'板卡：{info.get("board_name") or "未命名"}\n'
                f'波形 {info["waveform_count"]} · 执行序列 {info["sequence_count"]} · 触发步进 {info["scan_count"]}\n\n'
                '导入将替换当前方案库和配置，并重新打开工作区。\n'
                '当前设置会先备份到 user_data/archive/workspaces。\n'
                '恢复后保持未连接；外部固件文件路径需在本机核对。')
            if info.get('warnings'):
                message.setDetailedText('\n'.join(info['warnings']))
            accept = message.addButton('备份并导入', QMessageBox.AcceptRole)
            cancel = message.addButton('取消', QMessageBox.RejectRole)
            message.setDefaultButton(cancel)
            message.exec()
            if message.clickedButton() is accept:
                self.import_workspace(snapshot)
        except (OSError, ValueError, TypeError) as exc:
            QMessageBox.warning(self, '全局导入未完成', str(exc))
