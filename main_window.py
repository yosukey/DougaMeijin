# main_window.py
import os
import shutil
import tempfile
import traceback
import logging
from typing import Optional, List
from pathlib import Path

from PySide6.QtCore import (
    Qt, QUrl, QSize, QStandardPaths, QThread, QEvent,
    QEventLoop, Slot, QTimer
)
from PySide6.QtGui import (
    QPixmap, QDesktopServices, QIcon, QAction
)
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QListWidgetItem, QLabel,
    QFileDialog, QMessageBox, QProgressDialog,
    QDialog, QTextBrowser, QDialogButtonBox,
    QVBoxLayout, QPushButton
)

from models import Project, Page
from persistence import (
    remove_pages_from_project,
    save_project_to_zip, load_project_from_zip
)
from utils import (
    natural_sort_key, load_waveform_data,
    save_waveform_cache, load_waveform_cache, remove_waveform_cache,
    gracefully_shutdown_thread, compare_versions, prune_stale_caches
)
from config import *

from main_window_ui import UiBuilder
from list_delegate import RichTextDelegate
from workers import UpdateChecker, FFmpegCheckWorker, SaveProjectWorker, AssetExportWorker
from audio_handlers import PlaybackHandler, RecorderHandler, AudioSessionManager
from debug_console import DebugConsoleDialog
from ffmpeg_downloader import FFmpegDownloaderDialog
from worker_handler import WorkerHandler
from page_list_manager import PageListManager

logger = logging.getLogger(__name__)

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        
        self.playback_handler = PlaybackHandler(self)
        self.recorder_handler = RecorderHandler(self)
        self.worker_handler = WorkerHandler(self)

        builder = UiBuilder()
        builder.setup_ui(self)
        
        self.page_list_manager = PageListManager(self, self.list_pages)
        self._connect_signals()

        self.rich_text_delegate = RichTextDelegate(self.list_pages)
        self.list_pages.setItemDelegate(self.rich_text_delegate)

        self.installEventFilter(self)
        QApplication.instance().installEventFilter(self)

        self.total_duration_label = QLabel("合計時間: 00:00")
        self.statusBar().addPermanentWidget(self.total_duration_label)
        self.statusBar().showMessage("準備完了")

        self._project: Optional[Project] = None
        self._project_zip_path: Optional[str] = None
        self._work_dir: Optional[Path] = None
        self._is_dirty = False
        self._current_state = "idle"
        self._playback_start_position_msec = 0
        self._ffmpeg_is_available = False
        self._cached_preview_path: Optional[str] = None
        self._cached_preview_pixmap: Optional[QPixmap] = None
        
        self._audio_init_retries = 0

        self.combo_audio_input.addItem("マイクを初期化中...")
        self.combo_audio_input.setEnabled(False)
        
        self.page_list_manager.refresh()
        
        self.setAcceptDrops(False)
        
        self.ffmpeg_check_thread = None
        self.ffmpeg_check_worker = None
        self.update_thread = None
        self.update_worker = None
        self._is_first_show = True
        self._initialization_started = False

        self.debug_console = DebugConsoleDialog(self)

        logger.info("MainWindow initialized. Waiting for initialization signal.")

    def _connect_signals(self):
        # Menu Actions
        self.act_new.triggered.connect(self._new_project)
        self.act_open.triggered.connect(self._open_project)
        self.act_save.triggered.connect(self._save_project)
        self.act_export.triggered.connect(self.worker_handler.start_export)
        self.act_export_assets.triggered.connect(self._export_assets)
        self.act_quit.triggered.connect(self.close)
        self.act_rescan_devices.triggered.connect(self.recorder_handler.setup_audio_devices)
        self.act_show_debug_console.triggered.connect(self._show_debug_console)
        self.act_homepage.triggered.connect(self._open_homepage)
        self.act_github.triggered.connect(self._open_github_page)
        self.act_about.triggered.connect(self._show_about_dialog)

        # Welcome Widget Buttons
        self.btn_welcome_new.clicked.connect(self._new_project)
        self.btn_welcome_open.clicked.connect(self._open_project)

        # Main Widget Buttons
        self.btn_add_pages.clicked.connect(self.page_list_manager.add_pages)
        self.btn_remove_pages.clicked.connect(self.page_list_manager.remove_selected_pages)
        self.btn_save.clicked.connect(self._save_project)
        self.btn_export.clicked.connect(self.worker_handler.start_export)
        self.btn_record_stop.clicked.connect(self.recorder_handler.toggle_recording)
        self.btn_play.clicked.connect(self.playback_handler.toggle_playback)
        self.btn_pause.clicked.connect(self.playback_handler.toggle_playback)
        self.btn_stop_playback.clicked.connect(self.playback_handler.stop_playback)

        # List/Widget Signals
        self.list_pages.model().rowsMoved.connect(self.page_list_manager.on_pages_reordered)
        self.list_pages.itemSelectionChanged.connect(self._on_selection_changed)
        self.list_pages.currentRowChanged.connect(self._on_select_page)
        self.list_pages.customContextMenuRequested.connect(self.page_list_manager.show_page_context_menu)
        self.left_widget.filesDropped.connect(self.worker_handler.start_image_import)
        self.waveform_widget.audioFileDropped.connect(self.worker_handler.handle_audio_file_drop)
        self.waveform_widget.seekRequested.connect(self.playback_handler.seek_position_ratio)
        
        # ComboBox Signals
        self.combo_resolution.currentTextChanged.connect(self._on_setting_changed)
        self.combo_transition.currentTextChanged.connect(self._on_setting_changed)

        self.recorder_handler.connect_ui_signals()

    def start_initialization(self):
        if self._initialization_started:
            logger.info("Initialization has already been started. Ignoring duplicate call.")
            return
        self._initialization_started = True
        
        self._perform_deferred_initialization()

    def _perform_deferred_initialization(self):
        logger.info("Performing deferred initialization...")
        self._start_ffmpeg_check()
        self._start_update_check()
        QTimer.singleShot(1500, self._start_audio_initialization)

    def showEvent(self, event):
        super().showEvent(event)
        
        if self._is_first_show:
            self.start_initialization()
            self._is_first_show = False

    def eventFilter(self, watched, event):
        if event.type() == QEvent.ShortcutOverride and getattr(event, "key", lambda: None)() == Qt.Key_Space:
            if self.isActiveWindow():
                event.accept()
                return True

        if event.type() in (QEvent.KeyPress, QEvent.KeyRelease) and event.key() == Qt.Key_Space:
            if self.isActiveWindow():
                if event.type() == QEvent.KeyPress and not event.isAutoRepeat():
                    if self._current_state in ("idle", "recording"):
                        self.recorder_handler.toggle_recording()
                return True

        return super().eventFilter(watched, event)

    def closeEvent(self, event):
        root_logger = logging.getLogger()
        for handler in root_logger.handlers[:]:
            if isinstance(handler, logging.StreamHandler):
                root_logger.removeHandler(handler)
        
        try:
            WAIT_TIMEOUT_MS = 5000
            UPDATE_CHECK_WAIT_TIMEOUT_MS = 9000

            gracefully_shutdown_thread(self.ffmpeg_check_thread, "FFmpeg Check", WAIT_TIMEOUT_MS)
            gracefully_shutdown_thread(self.worker_handler.audio_thread, "Audio Processing", WAIT_TIMEOUT_MS)
            gracefully_shutdown_thread(self.worker_handler.import_thread, "Image Import", WAIT_TIMEOUT_MS)
            gracefully_shutdown_thread(self.worker_handler.audio_import_thread, "Audio Import", WAIT_TIMEOUT_MS)
            gracefully_shutdown_thread(self.worker_handler.waveform_thread, "Waveform Loader", WAIT_TIMEOUT_MS)

            if self.worker_handler.export_thread and self.worker_handler.export_thread.isRunning():
                logger.info("Export is running. Requesting cancellation...")
                if self.worker_handler.export_worker:
                    self.worker_handler.export_worker.request_cancel()
            gracefully_shutdown_thread(self.worker_handler.export_thread, "Export", WAIT_TIMEOUT_MS)
            
            gracefully_shutdown_thread(self.update_thread, "Update Check", UPDATE_CHECK_WAIT_TIMEOUT_MS)
            
            if self._is_dirty:
                if not self._prompt_save_changes():
                    event.ignore()
                    return

            self.recorder_handler.stop_hardware()
            self.playback_handler.stop_playback()

            self._cleanup_work_dir()

            if hasattr(self, 'debug_console'):
                self.debug_console.deleteLater()

            event.accept()

        except Exception as e:
            logger.critical("--- Unhandled Exception in closeEvent ---", exc_info=True)
            event.accept()

    @Slot()
    def _initialize_audio_devices(self) -> bool:
        logger.info("Initializing audio devices...")
        self.combo_audio_input.setEnabled(False)
        self.combo_audio_input.clear()
        self.combo_audio_input.addItem("マイクを検索中...")

        init_error: Optional[str] = None

        try:
            self.recorder_handler.initialize_devices()
        except Exception as exc:
            init_error = str(exc)
            logger.error("Failed to initialize audio devices.", exc_info=True)
            self.combo_audio_input.clear()
            self.combo_audio_input.addItem("録音デバイスの初期化に失敗しました")
            self.combo_audio_input.setEnabled(False)
        else:
            is_device_found = self.recorder_handler.has_devices()
            if is_device_found:
                self.combo_audio_input.setEnabled(True)
            else:
                self.combo_audio_input.setEnabled(False)

        if init_error:
            self.statusBar().showMessage("録音デバイスの初期化に失敗しました", STATUS_BAR_MSG_DURATION_MS)
            QMessageBox.critical(
                self,
                "録音デバイスの初期化に失敗しました",
                (
                    "録音デバイスの初期化中にエラーが発生しました。"
                    "マイク機能を利用できません。\n\n"
                    f"詳細: {init_error}\n\n"
                    "アプリを再インストールするか、Qt の audio プラグインが"
                    "ウイルス対策ソフトなどにより隔離されていないかを確認してください。"
                )
            )

    def _start_audio_initialization(self):
        MAX_RETRIES = 2
        RETRY_DELAY_MS = 2500

        if self.sender() == self.act_rescan_devices:
            self._audio_init_retries = 0

        self._initialize_audio_devices()
        is_device_found = self.recorder_handler.has_devices()
        if is_device_found:
            self._audio_init_retries = 0
            return

        if self._audio_init_retries < MAX_RETRIES:
            self._audio_init_retries += 1
            logger.warning(f"Audio init failed. Retrying in {RETRY_DELAY_MS}ms... (Attempt {self._audio_init_retries}/{MAX_RETRIES})")
            
            self.combo_audio_input.clear()
            self.combo_audio_input.addItem(f"再試行中 ({self._audio_init_retries})...")
            
            QTimer.singleShot(RETRY_DELAY_MS, self._start_audio_initialization)
        else:
            logger.warning("All audio initialization retries failed. Recording will be disabled.")
            self.combo_audio_input.clear()
            self.combo_audio_input.addItem("マイクの初期化に失敗")
            self.combo_audio_input.setToolTip(
                "マイクがPCに接続され、有効になっているか確認してください。\n"
                "その後、「ツール」メニューから再スキャンを実行できます。"
            )

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._update_preview_image()

    def _start_update_check(self):
        self.update_thread = QThread(self)
        self.update_worker = UpdateChecker()
        self.update_worker.moveToThread(self.update_thread)

        self.update_worker.finished.connect(self._on_update_check_finished)
        self.update_worker.error.connect(self._on_update_check_error)
        self.update_thread.started.connect(self.update_worker.process)

        self.update_worker.finished.connect(self.update_thread.quit)
        self.update_worker.error.connect(self.update_thread.quit)
        self.update_thread.finished.connect(self.update_thread.deleteLater)
        
        self.update_worker.finished.connect(self.update_worker.deleteLater)
        self.update_worker.error.connect(self.update_worker.deleteLater)

        self.update_thread.start()
        logger.info("Started background update check.")

    @Slot(str, str)
    def _on_update_check_finished(self, latest_version: str, release_url: str):
        logger.info(f"Update check finished. Latest version: {latest_version}, Current: {APP_VERSION}")
        if compare_versions(latest_version, APP_VERSION) > 0:
            msg_box = QMessageBox(self)
            msg_box.setIcon(QMessageBox.Icon.Information)
            msg_box.setWindowTitle("新しいバージョンのお知らせ")
            msg_box.setText(
                f"<h3>新しいバージョンが利用可能です</h3>"
                f"<p>新しいバージョン <b>{latest_version}</b> がリリースされています。<br>"
                f"（現在ご使用のバージョンは {APP_VERSION} です）</p>"
                f"<p>ダウンロードページを開いて詳細を確認しますか？</p>"
            )
            msg_box.setTextFormat(Qt.TextFormat.RichText)
            msg_box.setStandardButtons(QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
            msg_box.setDefaultButton(QMessageBox.StandardButton.Yes)
            
            if msg_box.exec() == QMessageBox.StandardButton.Yes:
                QDesktopServices.openUrl(QUrl(release_url))
        
        self.update_thread = None
        self.update_worker = None

    @Slot(str)
    def _on_update_check_error(self, error_message: str):
        logger.warning(f"Update check failed: {error_message}")
        self.update_thread = None
        self.update_worker = None

    def bring_to_front(self):
        self.show()
        if self.isMinimized():
            self.showNormal()
        self.activateWindow()
        self.raise_()

    def open_project_from_path(self, path: str):
            
        if not Path(path).exists():
            QMessageBox.critical(self, "エラー", f"ファイルが見つかりません:\n{path}")
            return

        new_work_dir = None
        try:
            new_work_dir_str = tempfile.mkdtemp(prefix="sbv_load_")
            new_work_dir = Path(new_work_dir_str)
            logger.info(f"Attempting to load project into temporary directory: {new_work_dir}")

            new_project = load_project_from_zip(path, new_work_dir_str)

        except IOError as e:
            if "プロジェクトの展開後サイズが大きすぎます" in str(e):
                reply = QMessageBox.question(
                    self,
                    "プロジェクトサイズの警告",
                    f"{e}\n\n"
                    "このファイルは通常よりサイズが大きく、"
                    "システムの動作を不安定にする可能性があります。\n"
                    "ご自身で作成した信頼できるファイルであることが確実な場合のみ、読み込みを続行してください。\n\n"
                    "読み込みを続行しますか？",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                    QMessageBox.StandardButton.No
                )
                if reply == QMessageBox.StandardButton.Yes:
                    try:
                        logger.info("User opted to proceed. Reloading project with size check skipped.")
                        new_project = load_project_from_zip(path, new_work_dir_str, skip_size_check=True)
                    except Exception as final_e:
                        QMessageBox.critical(self, "プロジェクト読み込みエラー", f"プロジェクトの読み込みに失敗しました:\n{final_e}")
                        logger.error(f"Failed to load oversized project: {final_e}", exc_info=True)
                        self._cleanup_temp_dir(new_work_dir)
                        return
                else:
                    logger.info("User canceled loading of oversized project.")
                    self._cleanup_temp_dir(new_work_dir)
                    return
            else:
                QMessageBox.critical(self, "プロジェクト読み込みエラー", f"ファイルの読み書き中にエラーが発生しました:\n{e}")
                logger.error(f"An IOError occurred during project load: {e}", exc_info=True)
                self._cleanup_temp_dir(new_work_dir)
                return

        except Exception as e:
            QMessageBox.critical(self, "プロジェクト読み込みエラー", f"プロジェクトの読み込みに失敗しました:\n{e}")
            logger.error(f"Failed to load project: {e}", exc_info=True)
            self._cleanup_temp_dir(new_work_dir)
            return

        logger.info(f"Load successful. Cleaning up old work directory: {self._work_dir}")
        self._cleanup_work_dir()

        self._work_dir = new_work_dir
        self._project = new_project
        self._project_zip_path = path

        self.recorder_handler.session_manager = AudioSessionManager(str(self._work_dir))
        
        prune_stale_caches(self._work_dir, self._project)
        
        self._mark_as_dirty(False)
        self.page_list_manager.refresh()
        logger.info(f"Project opened successfully: {path}")

    def _cleanup_temp_dir(self, temp_dir_path: Optional[Path]):
        if temp_dir_path and temp_dir_path.exists():
            try:
                shutil.rmtree(temp_dir_path, ignore_errors=True)
                logger.info(f"Cleaned up failed load directory: {temp_dir_path}")
            except Exception as cleanup_e:
                logger.error(f"Failed to cleanup temp dir {temp_dir_path}. Reason: {cleanup_e}")

    def _start_ffmpeg_check(self):
        if hasattr(self, 'ffmpeg_path_label'):
            self.ffmpeg_path_label.setText("FFmpeg: 確認中...")
        
        self.ffmpeg_check_thread = QThread(self)
        self.ffmpeg_check_worker = FFmpegCheckWorker()
        self.ffmpeg_check_worker.moveToThread(self.ffmpeg_check_thread)

        self.ffmpeg_check_worker.finished.connect(self._on_ffmpeg_check_finished)
        self.ffmpeg_check_thread.started.connect(self.ffmpeg_check_worker.process)
        
        self.ffmpeg_check_worker.finished.connect(self.ffmpeg_check_thread.quit)
        self.ffmpeg_check_thread.finished.connect(self.ffmpeg_check_thread.deleteLater)
        self.ffmpeg_check_worker.finished.connect(self.ffmpeg_check_worker.deleteLater)
        
        self.ffmpeg_check_thread.start()

    @Slot(str)
    def _on_ffmpeg_check_finished(self, path: str):
        if path:
            display_path = path.replace("\\", "/")
            if hasattr(self, 'ffmpeg_path_label'):
                self.ffmpeg_path_label.setText(f"FFmpeg: {display_path}")
            logger.info(f"FFmpeg is available at: {path}")
            self._ffmpeg_is_available = True
            
        else:
            logger.warning("FFmpeg not found. Launching downloader.")
            downloader_dialog = FFmpegDownloaderDialog(self)
            downloader_dialog.exec()

            if downloader_dialog.result is True:
                self._start_ffmpeg_check()
                return
            else:
                QMessageBox.critical(
                    None,
                    "必須コンポーネントがありません",
                    "FFmpegのセットアップが完了しなかったため、アプリケーションを終了します。\n"
                    "動画の作成にはFFmpegが必須です。"
                )
                QApplication.instance().quit()
                return
        
        self.ffmpeg_check_thread = None
        self.ffmpeg_check_worker = None

    def _setup_work_dir(self):
        self._cleanup_work_dir()
        self._work_dir = Path(tempfile.mkdtemp(prefix="sbv_work_"))
        logger.info(f"Created new work directory: {self._work_dir}")

    def _cleanup_work_dir(self):
        if self._work_dir and self._work_dir.exists():
            try:
                shutil.rmtree(self._work_dir, ignore_errors=False)
                logger.info(f"Cleaned up work directory: {self._work_dir}")
            except Exception as e:
                logger.error(f"Failed to cleanup work dir {self._work_dir}. Reason: {e}")
        self._work_dir = None

    @Slot()
    def _show_debug_console(self):
        self.debug_console.show()
        self.debug_console.raise_()
        self.debug_console.activateWindow()

    def _set_ui_state(self, state: str):
        self._current_state = state
        is_recording = (state == "recording")
        is_playing = (state == "playing")
        is_paused = (state == "paused")
        is_idle = (state == "idle")

        can_interact_project = is_idle
        can_export = can_interact_project and self._ffmpeg_is_available
        can_save = self._is_dirty and can_interact_project
        can_export_assets = can_interact_project and self._project_zip_path is not None and not self._is_dirty

        if hasattr(self, 'left_widget'):
            self.left_widget.setEnabled(can_interact_project)

        # General UI elements
        self.list_pages.setEnabled(can_interact_project)
        self.btn_add_pages.setEnabled(can_interact_project)
        self.btn_save.setEnabled(can_save)
        self.btn_export.setEnabled(can_export)
        self.combo_resolution.setEnabled(can_interact_project)
        self.combo_transition.setEnabled(can_interact_project)
        
        can_select_device = is_idle and self.recorder_handler.has_devices()
        self.combo_audio_input.setEnabled(can_select_device)
        
        if hasattr(self, 'act_rescan_devices'):
             self.act_rescan_devices.setEnabled(is_idle)
        
        self.waveform_widget.setEnabled(is_idle or is_paused or is_playing)
        self.waveform_widget.setAcceptDrops(is_idle)

        # Menu actions
        self.act_new.setEnabled(can_interact_project)
        self.act_open.setEnabled(can_interact_project)
        self.act_save.setEnabled(can_save)
        self.act_export.setEnabled(can_export)
        self.act_export_assets.setEnabled(can_export_assets)
        
        if can_interact_project:
            self._update_contextual_buttons()
        else:
            self.btn_remove_pages.setEnabled(False)
            self.btn_play.setEnabled(is_paused)
            self.btn_record_stop.setEnabled(False)
        
        if is_recording:
            self.btn_record_stop.setEnabled(True)

    def _update_contextual_buttons(self):
        can_remove = self.list_pages.currentRow() >= 0
        self.btn_remove_pages.setEnabled(can_remove)

        row = self.list_pages.currentRow()
        if not self._project or row < 0:
            self.btn_play.setEnabled(False)
            self.btn_record_stop.setEnabled(True)
            return

        page = self._project.pages[row]
        has_audio = bool(page.audio and page.duration and page.duration > MIN_AUDIO_DURATION_SEC)
        
        self.btn_play.setEnabled(has_audio)
        self.btn_record_stop.setEnabled(not page.locked)

    def _update_window_title(self):
        title_base = f"{APP_NAME} v{APP_VERSION}"
        project_name = f"[{DEFAULT_PROJECT_NAME}]"
        if self._project_zip_path:
            project_name = Path(self._project_zip_path).name
        dirty_marker = " *" if self._is_dirty else ""
        self.setWindowTitle(f"{project_name}{dirty_marker} - {title_base}")

    def _mark_as_dirty(self, dirty=True):
        if self._is_dirty == dirty:
            return
            
        self._is_dirty = dirty
        self._update_window_title()
        self._set_ui_state(self._current_state)

    def _new_project(self):
        if self._is_dirty:
            if not self._prompt_save_changes(): return
        logger.info("Creating new project...")
        self._project = None
        self._project_zip_path = None
        self._cleanup_work_dir()
        self._setup_work_dir()

        self.recorder_handler.session_manager = AudioSessionManager(str(self._work_dir))

        self._project = Project()
        self._mark_as_dirty(False)
        self.page_list_manager.refresh()
        logger.info("New project created and initialized.")

    def _open_project(self):
        if self._is_dirty:
            if not self._prompt_save_changes(): return
        
        documents_path = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.DocumentsLocation)
        file_filter_str = f"{PROJECT_FILTER_NAME} (*{PROJECT_FILE_EXTENSION})"
        zip_path, _ = QFileDialog.getOpenFileName(
            self, 
            "プロジェクトファイルを開く",
            documents_path,
            file_filter_str
        )
        if zip_path:
            self.open_project_from_path(zip_path)

    def _perform_save(self, path: str, skip_size_check: bool = False) -> bool:
        if not self._project or not self._work_dir:
            return False

        progress = QProgressDialog("プロジェクトサイズを確認中...", "キャンセル", 0, 0, self)
        progress.setWindowTitle("保存中")
        progress.setWindowModality(Qt.WindowModal)
        progress.setMinimumDuration(0)
        progress.setAutoClose(False)
        progress.setAutoReset(False)

        thread = QThread(self)
        worker = SaveProjectWorker(str(self._work_dir), self._project, path,
                                   skip_size_check=skip_size_check)
        worker.moveToThread(thread)

        result_holder = {"success": False, "message": ""}
        loop = QEventLoop(self)

        def on_finished(success, message):
            result_holder["success"] = success
            result_holder["message"] = message
            loop.quit()

        def on_progress(current, total, message):
            if total > 0:
                progress.setMaximum(total)
                progress.setValue(current)
            else:
                progress.setMaximum(0)
                progress.setValue(0)
            progress.setLabelText(message)

        def on_cancel():
            worker.request_cancel()
            progress.setLabelText("キャンセル処理中...")
            progress.setCancelButton(None)

        worker.finished.connect(on_finished)
        worker.update_progress.connect(on_progress)
        thread.started.connect(worker.process)
        progress.canceled.connect(on_cancel)

        logger.info(f"Starting background save to: {path}")
        progress.show()
        thread.start()
        loop.exec()
        progress.close()
        thread.quit()
        thread.wait(5000)
        thread.deleteLater()

        if result_holder["success"]:
            self._project_zip_path = path
            self.statusBar().showMessage("プロジェクトを保存しました", STATUS_BAR_SAVE_MSG_DURATION_MS)
            self._mark_as_dirty(False)
            logger.info("Save successful.")
            return True
        else:
            message = result_holder["message"]

            if message.startswith("SIZE_LIMIT:"):
                size_gb_str = message.split(":")[1]
                limit_gb = MAX_UNCOMPRESSED_PROJECT_SIZE_BYTES / (1024**3)
                reply = QMessageBox.warning(
                    self,
                    "プロジェクトサイズの警告",
                    f"このプロジェクトのサイズ ({size_gb_str} GB) は非常に大きく、"
                    f"推奨上限 ({limit_gb:.0f} GB) を超えています。\n"
                    "環境によっては、このプロジェクトファイルを再度開く際に問題が発生する可能性があります。\n\n"
                    "このまま保存を続行しますか？",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
                    QMessageBox.StandardButton.Cancel
                )
                if reply == QMessageBox.StandardButton.Yes:
                    return self._perform_save(path, skip_size_check=True)
                else:
                    self.statusBar().showMessage("保存がキャンセルされました", STATUS_BAR_MSG_DURATION_MS)
                    return False

            if message == "SAVE_CANCELED":
                self.statusBar().showMessage("保存がキャンセルされました", STATUS_BAR_MSG_DURATION_MS)
                return False

            QMessageBox.critical(self, "保存エラー", f"プロジェクトの保存に失敗しました:\n{message}")
            logger.error(f"Project save failed: {message}")
            return False

    def _save_project(self) -> bool:
        if not self._project:
            return False
        if not self._is_dirty:
            self.statusBar().showMessage("変更点はありません", STATUS_BAR_MSG_DURATION_MS)
            return True

        if self._project_zip_path and not Path(self._project_zip_path).exists():
            QMessageBox.information(
                self,
                "ファイルが見つかりません",
                "元のプロジェクトファイルが見つかりませんでした。\n"
                "新しく保存先を選択してください。"
            )
            return self._save_project_as()
            
        if not self._project_zip_path:
            return self._save_project_as()
        else:
            return self._perform_save(self._project_zip_path)

    def _save_project_as(self) -> bool:
        if not self._project:
            return False

        documents_path = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.DocumentsLocation)
        default_save_path = os.path.join(documents_path, f"{DEFAULT_PROJECT_NAME}{PROJECT_FILE_EXTENSION}")
        
        file_filter_str = f"{PROJECT_FILTER_NAME} (*{PROJECT_FILE_EXTENSION})"
        zip_path, _ = QFileDialog.getSaveFileName(
            self, 
            "名前を付けてプロジェクトを保存",
            default_save_path,
            file_filter_str
        )
        
        if zip_path:
            if os.path.exists(zip_path):
                reply = QMessageBox.question(
                    self,
                    "ファイルの上書き確認",
                    f"ファイルはすでに存在します:\n{os.path.basename(zip_path)}\n\n上書きしてもよろしいですか？",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                    QMessageBox.StandardButton.No
                )
                if reply == QMessageBox.StandardButton.No:
                    return False
            return self._perform_save(zip_path)
        else:
            return False

    def _prompt_save_changes(self) -> bool:
        reply = QMessageBox.question(self, "変更の保存", "プロジェクトに変更があります。保存しますか？",
                                     QMessageBox.Save | QMessageBox.Discard | QMessageBox.Cancel,
                                     QMessageBox.Save)
        if reply == QMessageBox.Save:
            return self._save_project()
        elif reply == QMessageBox.Cancel:
            return False
        
        logger.info("Discarding unsaved changes.")
        return True

    @Slot()
    def _export_assets(self):
        if not self._project_zip_path or self._is_dirty:
            QMessageBox.information(
                self,
                "素材ファイルのエクスポート",
                "この機能を利用するには、まずプロジェクトを保存し、\n"
                "未保存の変更がない状態にしてください。"
            )
            return

        if not self._project or not self._project.pages:
            QMessageBox.warning(self, "エクスポートエラー", "プロジェクトにエクスポート対象のページがありません。")
            return

        documents_path = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.DocumentsLocation)
        base_dir_str = QFileDialog.getExistingDirectory(
            self,
            "素材のエクスポート先フォルダを選択",
            documents_path
        )

        if not base_dir_str:
            self.statusBar().showMessage("エクスポートがキャンセルされました", STATUS_BAR_MSG_DURATION_MS)
            return

        project_basename = Path(self._project_zip_path).stem
        dest_dir = Path(base_dir_str) / project_basename
        
        try:
            dest_dir.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            QMessageBox.critical(self, "フォルダ作成エラー", f"エクスポート用フォルダの作成に失敗しました:\n{dest_dir}\n\n詳細: {e}")
            return
        
        pages_data = []
        for index, page in enumerate(self._project.pages):
            page_info = {"page_number": index + 1, "image": page.image}
            if page.audio and page.duration and page.duration > MIN_AUDIO_DURATION_SEC:
                page_info["audio"] = page.audio
            pages_data.append(page_info)

        self._set_ui_state("processing")

        progress = QProgressDialog("素材をエクスポート中...", None, 0, len(pages_data), self)
        progress.setWindowTitle("エクスポート中")
        progress.setWindowModality(Qt.WindowModal)
        progress.setMinimumDuration(0)
        progress.setAutoClose(False)
        progress.setAutoReset(False)

        thread = QThread(self)
        worker = AssetExportWorker(pages_data, str(self._work_dir), str(dest_dir))
        worker.moveToThread(thread)

        result_holder = {"exported_count": 0, "error_messages": []}
        loop = QEventLoop(self)

        def on_progress(current, total, message):
            progress.setMaximum(total)
            progress.setValue(current)
            progress.setLabelText(message)

        def on_finished(exported_count, error_messages):
            result_holder["exported_count"] = exported_count
            result_holder["error_messages"] = error_messages
            loop.quit()

        worker.update_progress.connect(on_progress)
        worker.finished.connect(on_finished)
        thread.started.connect(worker.process)

        progress.show()
        thread.start()
        loop.exec()
        progress.close()
        thread.quit()
        thread.wait(5000)
        thread.deleteLater()

        self._set_ui_state("idle")

        exported_count = result_holder["exported_count"]
        error_messages = result_holder["error_messages"]
        if not error_messages:
            QMessageBox.information(
                self,
                "エクスポート完了",
                f"{exported_count}個の素材ファイルのエクスポートが完了しました。\n"
                f"出力先: {dest_dir}"
            )
        else:
            summary = (
                f"{exported_count}個のファイルは正常にエクスポートされましたが、"
                f"{len(error_messages)}件のエラーが発生しました。"
            )
            details = "\n".join(error_messages)
            msg_box = QMessageBox(self)
            msg_box.setIcon(QMessageBox.Warning)
            msg_box.setWindowTitle("エクスポート完了（一部エラーあり）")
            msg_box.setText(summary)
            msg_box.setInformativeText(details)
            msg_box.exec()

        self.statusBar().showMessage("エクスポート完了", STATUS_BAR_SAVE_MSG_DURATION_MS)

    def _open_homepage(self):
        logger.info(f"Opening homepage: {HOMEPAGE_URL}")
        url = QUrl(HOMEPAGE_URL)
        QDesktopServices.openUrl(url)

    def _open_github_page(self):
        url_string = f"https://github.com/{GITHUB_REPO_ID}"
        logger.info(f"Opening GitHub page: {url_string}")
        url = QUrl(url_string)
        QDesktopServices.openUrl(url)

    def _show_about_dialog(self):
        dialog = QDialog(self)
        dialog.setWindowTitle("謝辞 (Acknowledgements)")
        dialog.setMinimumSize(600, 450)
        
        layout = QVBoxLayout(dialog)
        text_browser = QTextBrowser()
        text_browser.setOpenExternalLinks(True)
        
        content = """
        <html><head><style>
            body { font-family: "Meiryo", sans-serif; line-height: 1.6; }
            h2 { background-color: #e1e1e1; padding: 5px; border-radius: 4px; }
            ul { list-style-type: none; padding-left: 10px; }
            li { margin-bottom: 5px; }
            a { color: #0078d7; text-decoration: none; }
            a:hover { text-decoration: underline; }
        </style></head><body>
            <h2>謝辞 (Acknowledgements)</h2>
            <p>
                このアプリケーションは、多くの素晴らしいオープンソースソフトウェアのおかげで成り立っています。
                開発者の皆様に心より感謝申し上げます。
            </p>
            
            <h3>主要なコンポーネント:</h3>
            <ul>
                <li><b>Python:</b> <a href="https://www.python.org/">www.python.org</a></li>
                <li><b>PySide6 (Qt for Python):</b> <a href="https://www.qt.io/qt-for-python">www.qt.io/qt-for-python</a> (LGPLv3)</li>
                <li><b>FFmpeg:</b> <a href="https://ffmpeg.org/">ffmpeg.org</a> （外部プログラムとして利用）</li>
            </ul>

            <h3>主なPythonライブラリ:</h3>
            <ul>
                <li><b>NumPy:</b> <a href="https://numpy.org/">numpy.org</a></li>
                <li><b>PyMuPDF (fitz):</b> <a href="https://pymupdf.readthedocs.io/">pymupdf.readthedocs.io</a></li>
            </ul>
            <hr>
            <p>
                各コンポーネントのライセンスは、それぞれの公式サイトをご参照ください。
            </p>
        </body></html>
        """
        
        text_browser.setHtml(content)
        
        button_box = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok)
        button_box.accepted.connect(dialog.accept)
        layout.addWidget(text_browser)
        layout.addWidget(button_box)
        dialog.exec()

    def _update_preview_image(self):
        row = self.list_pages.currentRow()
        if not self._project or not self._project.pages or row < 0 or not self._work_dir:
            return

        page = self._project.pages[row]
        img_abs = self._work_dir / page.image
        
        self.label_image.setText("")

        if not img_abs.exists():
            self.label_image.setPixmap(QPixmap())
            self.label_image.setText(f"(画像が見つかりません)\n{img_abs}")
            return
        
        img_abs_str = str(img_abs)
        if self._cached_preview_path != img_abs_str:
            self._cached_preview_pixmap = QPixmap(img_abs_str)
            self._cached_preview_path = img_abs_str

        if self._cached_preview_pixmap.isNull():
            self.label_image.setText(f"(画像の読み込み失敗)\n{img_abs}")
            return

        scaled_pixmap = self._cached_preview_pixmap.scaled(
            self.label_image.size(),
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation
        )
        self.label_image.setPixmap(scaled_pixmap)

    def _on_select_page(self, row: int):
        self.playback_handler.stop_playback()

        self._playback_start_position_msec = 0
        self.waveform_widget.update_position(NO_PLAYBACK_POSITION)

        self._update_contextual_buttons()

        if not self._project or not self._project.pages or row < 0 or not self._work_dir:
            self.label_image.setText("ページがありません。画像を追加してください。")
            self.label_image.setPixmap(QPixmap())
            self.waveform_widget.clear_waveform() 
            return

        page = self._project.pages[row]
        
        has_audio = bool(page.audio and page.duration and page.duration > MIN_AUDIO_DURATION_SEC and (self._work_dir / page.audio).exists())

        if has_audio:
            waveform_data = load_waveform_cache(self._work_dir, page.page_id)
            
            if waveform_data is not None:
                duration = page.duration if page.duration is not None else 0.0
                self.waveform_widget.set_waveform(waveform_data, duration)
            else:
                self.waveform_widget.clear_waveform()
                audio_abs = self._work_dir / page.audio
                self.worker_handler.start_waveform_load(
                    str(audio_abs),
                    self.waveform_widget.width(),
                    page.page_id
                )
        else:
            self.waveform_widget.clear_waveform()

        self._update_preview_image()

    def _on_selection_changed(self):
        self._update_contextual_buttons()
        
    def _create_project_from_images(self, imgs: List[str]):
        if not imgs: return
        self._setup_work_dir()
        self._project = Project()
        self.worker_handler.start_image_import(imgs)
        
    def _reset_page_audio_state_on_error(self, page_id: str):
        logger.info(f"Resetting page audio state due to error for page_id: {page_id}")
        if self._work_dir:
            remove_waveform_cache(self._work_dir, page_id)

        if self._project:
            page_and_index = next(((i, p) for i, p in enumerate(self._project.pages) if p.page_id == page_id), None)
            
            if page_and_index:
                row, page = page_and_index
                page.audio = None
                page.duration = None
                page.audio_source_info = None
                self.page_list_manager.update_list_item_text(row)
                self.page_list_manager.update_total_duration()
                if self.list_pages.currentRow() == row:
                    self.waveform_widget.clear_waveform()
            else:
                logger.warning(f"_reset_page_audio_state_on_error called for a non-existent page (ID: {page_id}).")

    def _on_setting_changed(self):
        if not self._project:
            return

        self._project.resolution = self.combo_resolution.currentText()

        selected_transition_text = self.combo_transition.currentText()
        new_transition_key = next(
            (key for key, value in TRANSITIONS.items() if value == selected_transition_text),
            "none"
        )
        self._project.transition = new_transition_key
        
        logger.info(f"Project settings changed. Resolution: {self._project.resolution}, Transition: {self._project.transition}")
        self._mark_as_dirty()
        self.page_list_manager.update_total_duration()

    def _current_page_abs_audio(self) -> Optional[str]:
        row = self.list_pages.currentRow()
        if row < 0 or not self._project or not self._work_dir: return None
        page = self._project.pages[row]
        
        audio_filename = f"{page.page_id}.wav"

        audio_dir = self._work_dir / DIR_AUDIO
        audio_dir.mkdir(exist_ok=True)
        return str(audio_dir / audio_filename)
