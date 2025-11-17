# worker_handler.py
import os
import logging
from typing import List
from pathlib import Path

from PySide6.QtCore import QObject, QThread, Qt, QStandardPaths
from PySide6.QtWidgets import (
    QApplication, QMessageBox, QProgressDialog, QDialog,
    QVBoxLayout, QLabel, QTextEdit, QDialogButtonBox, QFileDialog
)

from models import Page
from workers import (
    AudioProcessingWorker, AudioImportWorker, ImageImportWorker, ExportWorker,
    WaveformLoadWorker
)
from config import (
    STATUS_BAR_MSG_DURATION_MS, STATUS_BAR_SAVE_MSG_DURATION_MS,
    MAX_FILES_TO_ADD_AT_ONCE, WARNING_TEXT, DEFAULT_EXPORT_FILENAME,
    MIN_AUDIO_DURATION_SEC
)
from utils import remove_waveform_cache, save_waveform_cache

logger = logging.getLogger(__name__)

class WorkerHandler(QObject):
    def __init__(self, main_win: 'MainWindow'):
        super().__init__()
        self.main_win = main_win

        self.audio_thread = None
        self.audio_worker = None
        self.import_thread = None
        self.import_worker = None
        self.export_thread = None
        self.export_worker = None
        self.audio_import_thread = None
        self.audio_import_worker = None
        self.waveform_thread = None
        self.waveform_worker = None
        
        self.export_progress_dialog = None

    def _start_worker(self, worker_instance, on_finished=None, on_error=None, on_progress=None):
        thread = QThread(self.main_win)
        worker = worker_instance
        worker.moveToThread(thread)

        worker.finished.connect(thread.quit)
        worker.error.connect(thread.quit)
        thread.finished.connect(thread.deleteLater)
        worker.finished.connect(worker.deleteLater)
        worker.error.connect(worker.deleteLater)
        thread.started.connect(worker.process)

        if on_finished:
            worker.finished.connect(on_finished)
        if on_error:
            worker.error.connect(on_error)
        
        if on_progress and hasattr(worker, 'update_progress'):
            worker.update_progress.connect(on_progress)
        
        thread.start()
        return worker, thread

    def start_image_import(self, paths: List[str]):
        if not self.main_win._project or not self.main_win._work_dir:
            return

        if len(paths) > MAX_FILES_TO_ADD_AT_ONCE:
            msg = (
                f"一度に追加できるファイル数は {MAX_FILES_TO_ADD_AT_ONCE} 個までです。\n"
                f"今回は {len(paths)} 個のファイルが選択されました。\n\n"
                "お手数ですが、ファイルを分割して追加してください。"
            )
            QMessageBox.warning(self.main_win, "ファイルの追加制限", msg)
            logger.warning(f"Import blocked. Too many files ({len(paths)} > {MAX_FILES_TO_ADD_AT_ONCE})")
            return
        
        new_source_basenames = {os.path.basename(p) for p in paths}
        existing_original_filenames = {p.original_filename for p in self.main_win._project.pages if p.original_filename}
        
        collisions = new_source_basenames.intersection(existing_original_filenames)
        
        if collisions:
            colliding_name_preview = list(collisions)[0]
            num_collisions = len(collisions)
            
            conflict_msg_detail = f"「{colliding_name_preview}」"
            if num_collisions > 1:
                conflict_msg_detail += f" 他 {num_collisions - 1} 件"

            main_text = f"{conflict_msg_detail} は、すでにプロジェクト内に存在するファイル名です。"
            info_text = (
                "もし、同じ画像を複数ページで利用したい場合は、既存ページを右クリックして「複製」すると、"
                "画像の追加コピー（容量消費）なしで同じ画像を使ったページを作成できます。\n\n"
                "いま追加操作したファイルを読み込みますか？"
            )

            msg_box = QMessageBox(self.main_win)
            msg_box.setIcon(QMessageBox.Question)
            msg_box.setWindowTitle("ファイル名の重複")
            msg_box.setText(main_text)
            msg_box.setInformativeText(info_text)
            
            btn_yes = msg_box.addButton("はい。読み込みます。", QMessageBox.ButtonRole.YesRole)
            btn_no = msg_box.addButton("いいえ。やっぱりやめます。", QMessageBox.ButtonRole.NoRole)
            msg_box.setDefaultButton(btn_no)
            
            msg_box.exec()

            if msg_box.clickedButton() == btn_no:
                self.main_win.statusBar().showMessage("インポートがキャンセルされました", STATUS_BAR_MSG_DURATION_MS)
                logger.info("Image import canceled by user due to filename collision.")
                return
        
        self.main_win._set_ui_state("processing")
        self.main_win.statusBar().showMessage("画像のインポートを開始します...", 0)
        logger.info(f"Starting image import worker for {len(paths)} files...")

        initial_page_count = len(self.main_win._project.pages)
        
        worker_instance = ImageImportWorker(str(self.main_win._work_dir), paths, initial_page_count)
        
        self.import_worker, self.import_thread = self._start_worker(
            worker_instance,
            on_finished=self._on_image_import_finished,
            on_error=self._on_image_import_error,
            on_progress=self._on_image_import_progress
        )

    def _on_image_import_progress(self, message: str):
        self.main_win.statusBar().showMessage(message, 0)
        logger.info(f"Import Progress: {message}")
        
    def _on_image_import_finished(self, new_pages: List[Page], initial_page_count: int, error_messages: List[str]):
        QApplication.restoreOverrideCursor()
        self.main_win.statusBar().showMessage(f"{len(new_pages)}個の画像を追加しました", STATUS_BAR_MSG_DURATION_MS)
        logger.info(f"Image import worker finished. Added {len(new_pages)} new pages.")

        if self.main_win._project:
            if new_pages:
                self.main_win._project.pages.extend(new_pages)
                self.main_win._mark_as_dirty()
                self.main_win.page_list_manager.refresh()
                self.main_win.list_pages.setCurrentRow(initial_page_count)
        
        if error_messages:
            self._show_import_error_summary(error_messages)

        self.main_win._set_ui_state("idle")
        self.import_thread = None
        self.import_worker = None

    def _on_image_import_error(self, error_message: str):
        QApplication.restoreOverrideCursor()
        self.main_win.setEnabled(True)
        self.main_win.statusBar().showMessage("インポートに失敗しました", STATUS_BAR_MSG_DURATION_MS)
        QMessageBox.critical(self.main_win, "インポートエラー", error_message)
        logger.error(f"Image import worker failed: {error_message}")
        self.main_win._set_ui_state("idle")

        self.import_thread = None
        self.import_worker = None

    def _show_import_error_summary(self, error_messages: List[str]):
        dialog = QDialog(self.main_win)
        dialog.setWindowTitle("インポート結果の要約")
        dialog.setMinimumSize(600, 300)

        layout = QVBoxLayout(dialog)
        
        label = QLabel("一部のファイルはインポートされませんでした。詳細は以下の通りです:", dialog)
        layout.addWidget(label)

        text_edit = QTextEdit(dialog)
        text_edit.setReadOnly(True)
        text_edit.setText("\n".join(error_messages))
        layout.addWidget(text_edit)

        button_box = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok, dialog)
        button_box.accepted.connect(dialog.accept)
        layout.addWidget(button_box)

        dialog.exec()

    def handle_audio_file_drop(self, source_path: str):
        logger.info(f"Audio file dropped onto waveform widget: {source_path}")
        
        row = self.main_win.list_pages.currentRow()
        if row < 0 or not self.main_win._project or not self.main_win._work_dir:
            self.main_win.statusBar().showMessage("音声を割り当てるページを選択してください", STATUS_BAR_MSG_DURATION_MS)
            logger.warning("Audio drop ignored. No page selected.")
            return

        page = self.main_win._project.pages[row]
        if page.locked:
            QMessageBox.warning(
                self.main_win, "インポートできません",
                "このページの音声は上書きがロックされています。\n"
                "インポートするには、ページを右クリックしてロックを解除してください。"
            )
            logger.warning("Audio drop ignored. Page is locked.")
            return
            
        has_audio = bool(page.audio and page.duration and page.duration > MIN_AUDIO_DURATION_SEC)
        if has_audio:
            reply = QMessageBox.question(
                self.main_win, "音声の上書き確認",
                "このページにはすでに音声が録音されています。\n"
                "新しい音声ファイルで上書きしますか？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No
            )
            if reply == QMessageBox.StandardButton.No:
                logger.info("Audio import canceled by user (overwrite prompt).")
                return
        
        session_manager = self.main_win.recorder_handler.session_manager
        if session_manager.is_active():
            QMessageBox.warning(self.main_win, "処理中", "現在別のアクティブな音声処理セッションがあります。完了するまでお待ちください。")
            return

        target_wav_path = session_manager.start_session(page)
        if not target_wav_path:
            self.main_win.statusBar().showMessage("音声セッションの開始に失敗しました", STATUS_BAR_MSG_DURATION_MS)
            return

        source_filename = os.path.basename(source_path)
        page.audio_source_info = f"インポート ({source_filename})"
            
        self.main_win.playback_handler.stop_playback()
        self.main_win.playback_handler.recreate_player()

        self.main_win._set_ui_state("processing")
        status_msg = f"音声ファイルをインポート中: {os.path.basename(source_path)}..."
        self.main_win.statusBar().showMessage(status_msg, 0)
        
        page_id = page.page_id

        worker_instance = AudioImportWorker(
            work_dir=str(self.main_win._work_dir),
            source_path=source_path,
            target_wav_path=target_wav_path,
            widget_width=self.main_win.waveform_widget.width(),
            page_id=page_id
        )
        self.audio_import_worker, self.audio_import_thread = self._start_worker(
            worker_instance,
            on_finished=self._on_audio_import_finished,
            on_error=self._on_audio_import_error
        )

    def start_audio_processing(self, audio_path: str, row: int):
        if not self.main_win._project or not (0 <= row < len(self.main_win._project.pages)):
            return
        
        page_id = self.main_win._project.pages[row].page_id
        
        self.main_win._set_ui_state("processing")
        self.main_win.statusBar().showMessage("音声ファイルを処理中...", 0)
        logger.info(f"Starting audio processing worker for recorded file: {audio_path}")

        worker_instance = AudioProcessingWorker(
            work_dir=str(self.main_win._work_dir),
            audio_path=audio_path,
            widget_width=self.main_win.waveform_widget.width(),
            page_id=page_id
        )
        self.audio_worker, self.audio_thread = self._start_worker(
            worker_instance,
            on_finished=self._on_record_processing_finished,
            on_error=self._on_audio_processing_error
        )

    def _finalize_audio_processing(self, page_id: str, rel_path: str, duration: float, waveform_data):
        self.main_win.recorder_handler.session_manager.commit_session()

        if not self.main_win._project:
            self.main_win._set_ui_state("idle")
            return
        
        page_and_index = next(((i, p) for i, p in enumerate(self.main_win._project.pages) if p.page_id == page_id), None)
        if not page_and_index:
            logger.warning(f"Audio processing finished for a non-existent page (ID: {page_id}). Result discarded.")
            return

        row, page = page_and_index
            
        self.main_win.statusBar().showMessage("音声処理が完了しました", STATUS_BAR_SAVE_MSG_DURATION_MS)
        logger.info(f"Audio processing finished for page {row} (ID: {page_id}). Path: {rel_path}, Duration: {duration:.2f}s")
        
        page.audio = rel_path
        page.duration = duration
        self.main_win._mark_as_dirty()

        self.main_win.page_list_manager.update_list_item_text(row)
        self.main_win.page_list_manager.update_total_duration()
        self.main_win.list_pages.setCurrentRow(row)
        
        if waveform_data is not None:
            if self.main_win._work_dir:
                save_waveform_cache(self.main_win._work_dir, page_id, waveform_data)
            self.main_win.waveform_widget.set_waveform(waveform_data, page.duration)
        else:
            self.main_win.waveform_widget.clear_waveform()

    def _on_record_processing_finished(self, page_id: str, rel_path: str, duration: float, waveform_data):
        self._finalize_audio_processing(page_id, rel_path, duration, waveform_data)

        self.audio_thread = None
        self.audio_worker = None
        self.main_win._set_ui_state("idle")

    def _on_audio_import_finished(self, page_id: str, rel_path: str, duration: float, waveform_data):
        self._finalize_audio_processing(page_id, rel_path, duration, waveform_data)

        self.audio_import_thread = None
        self.audio_import_worker = None
        self.main_win._set_ui_state("idle")

    def _on_audio_import_error(self, page_id: str, error_message: str):
        QMessageBox.critical(self.main_win, "音声インポートエラー", error_message)
        self.main_win.statusBar().showMessage("音声インポートに失敗しました", STATUS_BAR_MSG_DURATION_MS)
        logger.error(f"Audio import worker failed for page_id {page_id}: {error_message}")
        
        if self.main_win._work_dir:
            remove_waveform_cache(self.main_win._work_dir, page_id)
            
        session_manager = self.main_win.recorder_handler.session_manager
        
        if session_manager.is_active() and session_manager.get_active_page_id() == page_id:
            original_state = session_manager.abort_session()
            
            page_and_index = next(((i, p) for i, p in enumerate(self.main_win._project.pages) if p.page_id == page_id), None)
            
            if self.main_win._project and page_and_index:
                row, page = page_and_index
                page.audio = original_state.get("audio")
                page.duration = original_state.get("duration")
                page.audio_source_info = original_state.get("audio_source_info")
                
                self.main_win.page_list_manager.update_list_item_text(row)
                self.main_win.page_list_manager.update_total_duration()
                
                if self.main_win.list_pages.currentRow() == row:
                    self.main_win._on_select_page(row)
        else:
             self.main_win._reset_page_audio_state_on_error(page_id)

        self.main_win._set_ui_state("idle")
        self.audio_import_thread = None
        self.audio_import_worker = None

    def _on_audio_processing_error(self, page_id: str, error_message: str):
        QMessageBox.critical(self.main_win, "音声処理エラー", error_message)
        logger.error(f"Audio processing worker failed for page_id {page_id}: {error_message}")
        
        if self.main_win._work_dir:
            remove_waveform_cache(self.main_win._work_dir, page_id)
            
        session_manager = self.main_win.recorder_handler.session_manager
        
        if session_manager.is_active() and session_manager.get_active_page_id() == page_id:
            logger.info("Audio processing failed. Attempting to restore from backup.")
            original_state = session_manager.abort_session()
            
            page_and_index = next(((i, p) for i, p in enumerate(self.main_win._project.pages) if p.page_id == page_id), None)

            if self.main_win._project and page_and_index:
                row, page = page_and_index
                page.audio = original_state.get("audio")
                page.duration = original_state.get("duration")
                page.audio_source_info = original_state.get("audio_source_info")
                logger.info(f"Restored page metadata for row {row} (ID: {page_id}).")
                self.main_win.page_list_manager.refresh()
                self.main_win.list_pages.setCurrentRow(row)
        else:
            self.main_win._reset_page_audio_state_on_error(page_id)
        
        self.main_win._set_ui_state("idle")
        self.audio_thread = None
        self.audio_worker = None

    def start_waveform_load(self, audio_path: str, widget_width: int, page_id: str):
        if self.waveform_thread and self.waveform_thread.isRunning():
            logger.warning("Waveform load is already in progress. Ignoring new request.")
            return

        worker_instance = WaveformLoadWorker(audio_path, widget_width, page_id)
        
        self.waveform_worker, self.waveform_thread = self._start_worker(
            worker_instance,
            on_finished=self._on_waveform_loaded,
            on_error=self._on_waveform_load_error
        )

    def _on_waveform_loaded(self, page_id: str, waveform_data):
        logger.info(f"Waveform data loaded for page_id: {page_id}")
        
        if not self.main_win._project:
            return # Project was closed

        current_row = self.main_win.list_pages.currentRow()
        if current_row < 0 or current_row >= len(self.main_win._project.pages):
            return # No selection or invalid row

        current_page = self.main_win._project.pages[current_row]
        
        # Only update the widget if the loaded data corresponds to the *currently* selected page
        if current_page.page_id == page_id:
            if waveform_data is not None:
                if self.main_win._work_dir:
                    save_waveform_cache(self.main_win._work_dir, page_id, waveform_data)
                
                duration = current_page.duration if current_page.duration is not None else 0.0
                self.main_win.waveform_widget.set_waveform(waveform_data, duration)
            else:
                self.main_win.waveform_widget.clear_waveform()
        else:
            logger.info(f"Loaded waveform for {page_id}, but user is now on {current_page.page_id}. Discarding.")

        self.waveform_thread = None
        self.waveform_worker = None

    def _on_waveform_load_error(self, page_id: str, error_message: str):
        logger.error(f"Failed to load waveform for page_id {page_id}: {error_message}")
        
        # Check if the error corresponds to the currently selected page
        if self.main_win._project:
            current_row = self.main_win.list_pages.currentRow()
            if 0 <= current_row < len(self.main_win._project.pages):
                current_page = self.main_win._project.pages[current_row]
                if current_page.page_id == page_id:
                    self.main_win.waveform_widget.clear_waveform()
        
        self.waveform_thread = None
        self.waveform_worker = None


    def start_export(self):
        if not self._validate_project_for_export():
            return

        dialog = QMessageBox(self.main_win)
        dialog.setIcon(QMessageBox.Warning)
        dialog.setWindowTitle("注意事項")
        dialog.setText(WARNING_TEXT)
        dialog.setStandardButtons(QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        dialog.button(QMessageBox.StandardButton.Yes).setText("了承のうえ動画書き出しへ進む")
        dialog.button(QMessageBox.StandardButton.No).setText("動画作成をやめる")
        dialog.setDefaultButton(QMessageBox.StandardButton.No)

        if dialog.exec() == QMessageBox.StandardButton.No:
            return

        documents_path = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.DocumentsLocation)
        default_export_path = os.path.join(documents_path, DEFAULT_EXPORT_FILENAME)
        out, _ = QFileDialog.getSaveFileName(self.main_win, "MP4動画を書き出し", default_export_path, "MP4 Video (*.mp4)")
        if not out:
            return

        self.main_win._set_ui_state("processing")
        QApplication.setOverrideCursor(Qt.WaitCursor)

        self.export_progress_dialog = QProgressDialog("動画を書き出しています...", "キャンセル", 0, 100, self.main_win)
        self.export_progress_dialog.setWindowModality(Qt.WindowModality.WindowModal)
        self.export_progress_dialog.setWindowTitle("書き出し中")
        self.export_progress_dialog.setMinimumDuration(0)
        
        self.export_progress_dialog.canceled.connect(self.request_export_cancel)
        
        self.export_worker = ExportWorker(self.main_win._project, str(self.main_win._work_dir), out)
        self.export_worker, self.export_thread = self._start_worker(
            self.export_worker,
            on_finished=self._on_export_finished,
            on_error=self._on_export_error,
            on_progress=self._on_export_progress_update
        )

        self.export_progress_dialog.show()

    def _validate_project_for_export(self) -> bool:
        if not self.main_win._project or not self.main_win._work_dir:
            QMessageBox.information(self.main_win, "書き出し", "書き出すプロジェクトがありません。")
            return False
        if not self.main_win._project.pages:
            QMessageBox.warning(self.main_win, "書き出しエラー", "プロジェクトにページがありません。")
            return False

        work_dir = self.main_win._work_dir
        pages_with_metadata = [
            (i + 1, p) for i, p in enumerate(self.main_win._project.pages)
            if p.audio and p.duration and p.duration >= MIN_AUDIO_DURATION_SEC
        ]
        
        missing_files_pages = [
            str(page_num) for page_num, page in pages_with_metadata
            if not (work_dir / page.audio).exists()
        ]

        if missing_files_pages:
            QMessageBox.critical(
                self.main_win,
                "書き出しエラー",
                f"音声ファイルが見つかりません。\n\n"
                f"以下のページの音声ファイルが破損しているか、見つかりませんでした。\n"
                f"ページ: {', '.join(missing_files_pages)}\n\n"
                "プロジェクトを再度読み込み直すか、該当ページの音声を再録音してください。"
            )
            return False

        pages_with_audio = [p for _, p in pages_with_metadata]
        if not pages_with_audio:
            QMessageBox.warning(self.main_win, "書き出しエラー", "どのページにも音声が録音されていません。")
            return False
        
        pages_without_audio_indices = [
            i + 1 for i, p in enumerate(self.main_win._project.pages)
            if not p.duration or p.duration < MIN_AUDIO_DURATION_SEC
        ]
        if pages_without_audio_indices:
            indices_str = ', '.join(map(str, pages_without_audio_indices))
            reply = QMessageBox.question(self.main_win, "無音ページの確認",
                f"以下のページには音声が録音されていません。これらのページはスキップされますが、よろしいですか？\n\nページ: {indices_str}",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.Yes)
            
            if reply == QMessageBox.StandardButton.No:
                return False
        
        return True

    def request_export_cancel(self):
        if self.export_worker:
            logger.info("Export cancel requested by user (progress dialog).")
            self.export_worker.request_cancel()
            self.main_win.statusBar().showMessage("動画の書き出しをキャンセルしています...", 0)
            if self.export_progress_dialog:
                self.export_progress_dialog.setCancelButton(None)

    def _on_export_progress_update(self, value: int, total: int, message: str):
        if self.export_progress_dialog:
            self.export_progress_dialog.setMaximum(total)
            self.export_progress_dialog.setValue(value)
            self.export_progress_dialog.setLabelText(message)

    def _on_export_finished(self, was_canceled: bool):
        try:
            self.export_progress_dialog.canceled.disconnect(self.request_export_cancel)
        except RuntimeError:
            pass
        self.export_progress_dialog.close()
        QApplication.restoreOverrideCursor()
        self.main_win._set_ui_state("idle")

        if was_canceled:
            QMessageBox.warning(self.main_win, "キャンセル", "動画の書き出しがキャンセルされました。")
        else:
            QMessageBox.information(self.main_win, "成功", "動画の書き出しが完了しました。")
            
        self.export_thread = None
        self.export_worker = None

    def _on_export_error(self, error_message: str):
        try:
            self.export_progress_dialog.canceled.disconnect(self.request_export_cancel)
        except RuntimeError:
            pass

        self.export_progress_dialog.close()
        QApplication.restoreOverrideCursor()
        self.main_win._set_ui_state("idle")
        QMessageBox.critical(self.main_win, "書き出し失敗", error_message)
        logger.error(f"Export worker failed: {error_message}")

        self.export_thread = None
        self.export_worker = None