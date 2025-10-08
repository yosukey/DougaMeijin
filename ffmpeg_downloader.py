# ffmpeg_downloader.py
import os
import shutil
import json
import zipfile
import hashlib
import tempfile
import uuid
import urllib.request
from urllib.error import URLError
from typing import Tuple
from pathlib import Path

from PySide6.QtCore import Qt, QThread, Slot
from PySide6.QtWidgets import (
    QMessageBox, QProgressDialog, QApplication, QDialog,
    QVBoxLayout, QLabel, QDialogButtonBox
)

import utils
from config import (
    FFMPEG_INSTALL_DIR, FFMPEG_API_URL, FFMPEG_TARGET_ZIP_FILENAME,
    FFMPEG_GLOBAL_CHECKSUM_FILENAME, FFMPEG_DOWNLOADER_USER_AGENT
)
from utils import gracefully_shutdown_thread

FFMPEG_DOWNLOAD_CANCELED = "ダウンロードがキャンセルされました。"


def check_ffmpeg_exists() -> bool:
    try:
        utils.ffmpeg_executable_path()
        return True
    except FileNotFoundError:
        return False

def _get_urls_and_hash() -> Tuple[str, str]:
    req = urllib.request.Request(FFMPEG_API_URL, headers={"User-Agent": FFMPEG_DOWNLOADER_USER_AGENT})
    with urllib.request.urlopen(req, timeout=15) as response:
        if response.status != 200:
            raise RuntimeError(f"GitHub APIへのアクセスに失敗しました (Status: {response.status})")
        data = json.loads(response.read())

    assets = data.get("assets", [])
    zip_url, checksums_url = None, None
    for asset in assets:
        if asset.get("name") == FFMPEG_TARGET_ZIP_FILENAME:
            zip_url = asset.get("browser_download_url")
        elif asset.get("name") == FFMPEG_GLOBAL_CHECKSUM_FILENAME:
            checksums_url = asset.get("browser_download_url")

    if not (zip_url and checksums_url):
        raise RuntimeError("API応答から必要なアセットが見つかりませんでした。")

    hash_req = urllib.request.Request(checksums_url, headers={"User-Agent": FFMPEG_DOWNLOADER_USER_AGENT})
    with urllib.request.urlopen(hash_req, timeout=15) as response:
        hash_text_data = response.read().decode("utf-8")

    for line in hash_text_data.splitlines():
        parts = line.strip().split()
        if len(parts) >= 2 and parts[-1].endswith(FFMPEG_TARGET_ZIP_FILENAME):
            return zip_url, parts[0]
    
    raise RuntimeError("チェックサムファイルからハッシュ値の取得に失敗しました。")

def _verify_hash(file_path: Path, expected_hash: str, progress_callback) -> bool:
    sha256 = hashlib.sha256()
    file_size = file_path.stat().st_size
    if file_size == 0: return False

    processed_size = 0
    with file_path.open("rb") as f:
        while chunk := f.read(4 * 1024 * 1024):
            sha256.update(chunk)
            processed_size += len(chunk)
            progress_callback(processed_size, file_size, f"ファイルを検証中... ({processed_size/file_size*100:.0f}%)")
    
    return sha256.hexdigest().lower() == expected_hash.lower()

def _install_zip(zip_path: Path):
    storage_path = utils.get_data_storage_path()
    final_install_dir = storage_path / FFMPEG_INSTALL_DIR
    temp_extract_dir = Path(tempfile.mkdtemp(dir=storage_path, prefix="ffmpeg_extract_"))

    try:
        with zipfile.ZipFile(zip_path, 'r') as zf:
            root_dir_in_zip = zf.infolist()[0].filename
            for member in zf.infolist():
                relative_path = Path(member.filename).relative_to(root_dir_in_zip)
                target_path = temp_extract_dir / relative_path
                if member.is_dir():
                    target_path.mkdir(parents=True, exist_ok=True)
                else:
                    target_path.parent.mkdir(parents=True, exist_ok=True)
                    with zf.open(member) as source, target_path.open("wb") as target:
                        shutil.copyfileobj(source, target)
        
        backup_dir = None
        if final_install_dir.exists():
            backup_dir = Path(f"{final_install_dir}_{uuid.uuid4().hex}.bak")
            final_install_dir.rename(backup_dir)
        
        try:
            temp_extract_dir.rename(final_install_dir)
        except Exception as e:
            if backup_dir and backup_dir.exists():
                backup_dir.rename(final_install_dir)
            raise e

        if backup_dir and backup_dir.exists():
            shutil.rmtree(backup_dir, ignore_errors=True)
    finally:
        if temp_extract_dir.exists():
            shutil.rmtree(temp_extract_dir, ignore_errors=True)


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
        from workers import FFmpegDownloadWorker
        
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