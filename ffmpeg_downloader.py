# ffmpeg_downloader.py
from PySide6.QtCore import Qt, QThread, Slot
from PySide6.QtWidgets import (
    QMessageBox, QProgressDialog, QDialog,
    QVBoxLayout, QLabel, QDialogButtonBox
)

from utils import gracefully_shutdown_thread
from workers import FFmpegDownloadWorker, FFMPEG_DOWNLOAD_CANCELED

class FFmpegDownloaderDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("FFmpeg セットアップ")
        self.setMinimumWidth(400)
        
        self.worker = None
        self.thread = None
        self.result = False

        layout = QVBoxLayout(self)
        self.label = QLabel("本アプリの動作には FFmpeg が必須です。\n最新版を（GitHubから）ダウンロードしますか？")
        self.label.setWordWrap(True)
        self.progress_dialog = None

        self.button_box = QDialogButtonBox(QDialogButtonBox.StandardButton.Yes | QDialogButtonBox.StandardButton.No)
        
        yes_button = self.button_box.button(QDialogButtonBox.StandardButton.Yes)
        yes_button.clicked.connect(self.start_download)
        
        no_button = self.button_box.button(QDialogButtonBox.StandardButton.No)
        no_button.clicked.connect(self.reject)

        layout.addWidget(self.label)
        layout.addWidget(self.button_box)

    def start_download(self):
        self.button_box.hide()
        if self.progress_dialog is None:
            self.progress_dialog = QProgressDialog(self)
            self.progress_dialog.setWindowModality(Qt.WindowModal)
            self.progress_dialog.setAutoClose(False)
            self.progress_dialog.setAutoReset(False)
            self.progress_dialog.canceled.connect(self.cancel_download)

        self.progress_dialog.show()
        self.progress_dialog.setValue(0)
        self.progress_dialog.setLabelText("準備中...")

        self.thread = QThread(self)
        self.worker = FFmpegDownloadWorker()
        self.worker.moveToThread(self.thread)

        self.worker.progress.connect(self.on_progress)
        self.worker.finished.connect(self.on_finished)
        self.thread.started.connect(self.worker.process)

        self.worker.finished.connect(self.thread.quit)
        self.thread.finished.connect(self.thread.deleteLater)
        self.worker.finished.connect(self.worker.deleteLater)
        
        self.thread.start()

    @Slot(int, int, str)
    def on_progress(self, value, total, message):
        if not self.progress_dialog:
            return

        self.progress_dialog.setLabelText(message)
        if total > 0:
            self.progress_dialog.setMaximum(total)
            self.progress_dialog.setValue(value)
        else:
            self.progress_dialog.setMaximum(0)

    @Slot(bool, str)
    def on_finished(self, success, message):
        if self.progress_dialog:
            self.progress_dialog.hide()
        if success:
            QMessageBox.information(self, "成功", f"{message}\nダイアログを閉じると、アプリが再開します。")
            self.result = True
        else:
            if message != FFMPEG_DOWNLOAD_CANCELED:
                QMessageBox.warning(self, "失敗", message)
            self.result = False
        self.accept()

    def cancel_download(self):
        if self.worker:
            self.worker.request_cancel()

    def closeEvent(self, event):
        self.cancel_download()
        gracefully_shutdown_thread(self.thread, "FFmpeg Download", timeout_ms=1000)
        
        if self.progress_dialog:
            self.progress_dialog.hide()
        super().closeEvent(event)