# audio_handlers.py
import os
import shutil
import wave
import uuid
import logging
from typing import Optional
from pathlib import Path
from PySide6.QtCore import QObject, QUrl, QTimer, QElapsedTimer, Signal, Qt, Slot
from PySide6.QtMultimedia import (
    QAudioDevice,
    QAudioSource,
    QMediaDevices,
    QAudioFormat,
    QAudio,
    QMediaPlayer,
    QAudioOutput
)
import numpy as np
from PySide6.QtWidgets import QMessageBox

from models import Page
from recorder import AudioRecorder
from config import (
    STATUS_BAR_MSG_DURATION_MS,
    MIN_RECORDING_DURATION_SEC, NO_PLAYBACK_POSITION,
    MIN_AUDIO_DURATION_SEC, DIR_AUDIO
)
from main_window_ui import (
    LEVEL_BAR_STYLE_RED, LEVEL_BAR_STYLE_YELLOW, LEVEL_BAR_STYLE_GREEN,
    REC_BUTTON_STYLE, STOP_BUTTON_STYLE
)
from utils import remove_waveform_cache

logger = logging.getLogger(__name__)


class AudioSessionManager:

    def __init__(self, work_dir: str):
        self._work_dir = Path(work_dir)
        self._is_active = False
        self._backup_info = {}

    def start_session(self, page: Page) -> Optional[str]:
        if self._is_active:
            logger.error("Another session is already active.")
            return None

        target_audio_filename = f"{page.page_id}.wav"
        audio_dir = self._work_dir / DIR_AUDIO
        audio_dir.mkdir(parents=True, exist_ok=True)
        target_abs_path = audio_dir / target_audio_filename

        self._backup_info = {
            "page_id": page.page_id,
            "original_rel_path": page.audio,
            "original_duration": page.duration,
            "original_source_info": page.audio_source_info,
            "target_abs_path": target_abs_path,
            "backup_abs_path": None
        }

        try:
            if page.audio:
                old_audio_abs_path = self._work_dir / page.audio
                if old_audio_abs_path.exists():
                    backup_path = target_abs_path.with_suffix(f".{uuid.uuid4().hex}.bak")
                    old_audio_abs_path.rename(backup_path)
                    self._backup_info["backup_abs_path"] = backup_path
                    logger.info(f"Backed up existing audio to: {backup_path}")
            
            remove_waveform_cache(self._work_dir, page.page_id)
            self._is_active = True
            return str(target_abs_path)

        except Exception as e:
            logger.error("Failed to start audio session, aborting.", exc_info=True)
            self._reset()
            return None

    def commit_session(self):
        if not self._is_active:
            return

        backup_path = self._backup_info.get("backup_abs_path")
        if backup_path and backup_path.exists():
            try:
                backup_path.unlink()
                logger.info(f"Session committed. Removed backup file: {backup_path}")
            except OSError as e:
                logger.warning(f"Could not remove audio backup file after commit: {e}")
        
        self._reset()

    def abort_session(self) -> dict:
        if not self._is_active:
            return {}

        logger.info("Aborting audio session, attempting to restore from backup.")
        backup_path: Optional[Path] = self._backup_info.get("backup_abs_path")
        target_path: Optional[Path] = self._backup_info.get("target_abs_path")

        if backup_path and backup_path.exists():
            try:
                backup_path.replace(target_path)
                logger.info(f"Successfully restored backup audio to: {target_path}")
            except (OSError, shutil.Error) as e:
                logger.critical(f"Could not restore audio from backup. Reason: {e}", exc_info=True)
                logger.critical(f"Manual recovery may be needed. Backup is at: {backup_path}")
        
        elif target_path and target_path.exists():
            try:
                target_path.unlink()
                logger.info(f"Removed intermediate audio file during abort: {target_path}")
            except OSError as e:
                logger.warning(f"Failed to remove intermediate audio file during abort: {e}")
        
        original_state = {
            "audio": self._backup_info.get("original_rel_path"),
            "duration": self._backup_info.get("original_duration"),
            "audio_source_info": self._backup_info.get("original_source_info")
        }
        
        self._reset()
        return original_state

    def get_active_page_id(self) -> Optional[str]:
        return self._backup_info.get("page_id") if self._is_active else None

    def get_target_abs_path(self) -> Optional[str]:
        target_path = self._backup_info.get("target_abs_path")
        return str(target_path) if self._is_active and target_path else None

    def _reset(self):
        self._is_active = False
        self._backup_info = {}

    def is_active(self) -> bool:
        return self._is_active


class PlaybackHandler(QObject):
    def __init__(self, main_win: 'MainWindow'):
        super().__init__()
        self.main_win = main_win
        self._player: Optional[QMediaPlayer] = None
        self._audio_output: Optional[QAudioOutput] = None
        
        self.recreate_player()

    def recreate_player(self):
        if self._player:
            self._player.stop()
            try:
                self._player.playbackStateChanged.disconnect(self._on_player_state_changed)
                self._player.positionChanged.disconnect(self._on_player_position_changed)
                self._player.errorOccurred.disconnect(self._on_player_error)
            except RuntimeError:
                pass
            self._player.deleteLater()
        
        if self._audio_output:
            self._audio_output.deleteLater()

        self._player = QMediaPlayer()
        self._audio_output = QAudioOutput()
        self._player.setAudioOutput(self._audio_output)
        
        self._player.playbackStateChanged.connect(self._on_player_state_changed)
        self._player.positionChanged.connect(self._on_player_position_changed)
        self._player.errorOccurred.connect(self._on_player_error)

    def toggle_playback(self):
        state = self._player.playbackState()
        main = self.main_win
        start_from_specific_position = main._playback_start_position_msec > 0

        if state == QMediaPlayer.PlaybackState.PlayingState:
            self._player.pause()
        else:
            if state == QMediaPlayer.PlaybackState.StoppedState:
                row = main.list_pages.currentRow()
                if row < 0 or not main._project or not main._work_dir: return
                page = main._project.pages[row]
                if not page.audio: return
                audio_path = main._work_dir / page.audio
                if not audio_path.exists():
                    QMessageBox.warning(main, "再生エラー", "音声ファイルが見つかりません。")
                    has_audio = bool(page.audio and page.duration and page.duration > MIN_AUDIO_DURATION_SEC)
                    main.btn_play.setEnabled(has_audio)
                    main._playback_start_position_msec = 0
                    return
                
                if start_from_specific_position:
                    try:
                        self._player.mediaStatusChanged.disconnect(self._handle_media_load_for_seek)
                    except RuntimeError:
                        pass
                    self._player.mediaStatusChanged.connect(self._handle_media_load_for_seek)
                
                self._player.setSource(QUrl.fromLocalFile(str(audio_path)))
            
            if state == QMediaPlayer.PlaybackState.PausedState and start_from_specific_position:
                self._player.setPosition(main._playback_start_position_msec)
                main._playback_start_position_msec = 0
            
            self._player.play()

    def stop_playback(self):
        if self._player:
            self._player.stop()
            self._player.setSource(QUrl())

    def get_player_state(self) -> QMediaPlayer.PlaybackState:
        if self._player:
            return self._player.playbackState()
        return QMediaPlayer.PlaybackState.StoppedState

    def seek_position_ratio(self, ratio: float):
        main = self.main_win
        row = main.list_pages.currentRow()
        if not main._project or row < 0: return
        page = main._project.pages[row]
        duration_msec = page.duration * 1000 if page.duration else 0

        if not self._player or duration_msec <= 0: return

        target_position_msec = int(duration_msec * ratio)

        current_state = self._player.playbackState()

        if current_state in (QMediaPlayer.PlaybackState.PlayingState, QMediaPlayer.PlaybackState.PausedState):
            self._player.setPosition(target_position_msec)
            self._player.play()
        else:
            main._playback_start_position_msec = target_position_msec
            main.waveform_widget.update_position(ratio)

    def _on_player_state_changed(self, state: QMediaPlayer.PlaybackState):
        main = self.main_win
        if state == QMediaPlayer.PlaybackState.PlayingState:
            main.play_pause_stack.setCurrentWidget(main.btn_pause)
            main.btn_stop_playback.show()
            main._set_ui_state("playing")

        elif state == QMediaPlayer.PlaybackState.PausedState:
            main.play_pause_stack.setCurrentWidget(main.btn_play)
            main.btn_stop_playback.show()
            main._set_ui_state("paused")

        else:
            main.play_pause_stack.setCurrentWidget(main.btn_play)
            main.btn_stop_playback.hide()
            
            main.waveform_widget.update_position(NO_PLAYBACK_POSITION)
            
            if not main.recorder_handler.is_recording() and main._current_state != "processing":
                main._set_ui_state("idle")

    def _on_player_position_changed(self, position: int):
        if self._player:
            duration = self._player.duration()
            if duration > 0:
                ratio = position / duration
                self.main_win.waveform_widget.update_position(ratio)

    def _on_player_error(self):
        if self._player:
            QMessageBox.critical(self.main_win, "再生エラー", self._player.errorString())

    def _handle_media_load_for_seek(self, status: QMediaPlayer.MediaStatus):
        if status in (QMediaPlayer.MediaStatus.LoadedMedia,
                      QMediaPlayer.MediaStatus.BufferedMedia):
            main = self.main_win
            if main._playback_start_position_msec > 0:
                duration = self._player.duration()
                target_pos = main._playback_start_position_msec
                if duration > 0:
                    safe_pos = max(0, min(target_pos, max(duration - 1, 0)))
                    self._player.setPosition(safe_pos)

            try:
                self._player.mediaStatusChanged.disconnect(self._handle_media_load_for_seek)
            except RuntimeError:
                pass
            
            main._playback_start_position_msec = 0


class RecorderHandler(QObject):
    def __init__(self, main_win: 'MainWindow'):
        super().__init__()
        self.main_win = main_win
        self._recorder: Optional[AudioRecorder] = None
        self._audio_devices = []
        
        self._record_timer = QTimer(self)
        self._record_timer.setInterval(100)
        self._record_timer.timeout.connect(self.update_record_time_slot)
        self._record_elapsed_timer = QElapsedTimer()

        self.session_manager: Optional[AudioSessionManager] = None

        self._media_devices = QMediaDevices(self)
        self._media_devices.audioInputsChanged.connect(self._handle_audio_devices_changed)

    def has_devices(self) -> bool:
        return bool(self._audio_devices)

    def connect_ui_signals(self):
        self.main_win.combo_audio_input.currentIndexChanged.connect(self.on_audio_device_changed)

    def initialize_devices(self):
        try:
            self.setup_audio_devices()
            self.recreate_recorder()
        except RuntimeError:
            self._audio_devices = []
            self._reset_recorder_reference()
            raise
        except Exception as e:
            self._audio_devices = []
            self._reset_recorder_reference()
            raise RuntimeError(f"録音デバイスの初期化に失敗しました: {e}") from e

    @Slot()
    def _handle_audio_devices_changed(self):
        logger.info("Hotplug event detected: Audio input devices changed.")
        combo = self.main_win.combo_audio_input
        
        current_device_id = None
        if combo.count() > 0:
            current_device_id = combo.currentData(Qt.UserRole)

        self.setup_audio_devices()

        new_index = -1
        if current_device_id:
            for i in range(combo.count()):
                if combo.itemData(i, Qt.UserRole) == current_device_id:
                    new_index = i
                    break
        
        if new_index != -1:
            if combo.currentIndex() != new_index:
                combo.setCurrentIndex(new_index)
            self.main_win.statusBar().showMessage("録音デバイスリストを更新しました", STATUS_BAR_MSG_DURATION_MS)
        else:
            if current_device_id is not None:
                self.main_win.statusBar().showMessage("選択中のデバイスが切断されました。デフォルトデバイスに切り替えました。", STATUS_BAR_MSG_DURATION_MS)
            else:
                self.main_win.statusBar().showMessage("新しい録音デバイスを検出しました", STATUS_BAR_MSG_DURATION_MS)


    def setup_audio_devices(self):
        main = self.main_win
        main.combo_audio_input.blockSignals(True)

        try:
            try:
                self._audio_devices = self._media_devices.audioInputs()
            except Exception as e:
                raise RuntimeError(f"録音デバイスの一覧取得に失敗しました: {e}") from e

            main.combo_audio_input.clear()

            if not self._audio_devices:
                main.combo_audio_input.addItem("利用可能なマイクがありません")
                main.combo_audio_input.setEnabled(False)
                return

            try:
                default_device = self._media_devices.defaultAudioInput()
                default_device_id = default_device.id()
            except Exception as e:
                default_device_id = None
                logger.warning(f"Failed to query default audio input: {e}")

            default_index = -1

            for i, device in enumerate(self._audio_devices):
                main.combo_audio_input.addItem(device.description())
                main.combo_audio_input.setItemData(i, device.id(), Qt.UserRole)
                if default_device_id is not None and device.id() == default_device_id:
                    default_index = i

            main.combo_audio_input.setEnabled(True)

            if default_index != -1:
                main.combo_audio_input.setCurrentIndex(default_index)
            elif self._audio_devices:
                main.combo_audio_input.setCurrentIndex(0)
        finally:
            main.combo_audio_input.blockSignals(False)

    def on_audio_device_changed(self, index):
        if not self._audio_devices or not (0 <= index < len(self._audio_devices)):
            return
        
        selected_device = self._audio_devices[index]
        self.recreate_recorder(selected_device)
        self.main_win.statusBar().showMessage(
            f"録音デバイスを「{selected_device.description()}」に変更しました",
            STATUS_BAR_MSG_DURATION_MS
        )

    def recreate_recorder(self, device: Optional[QAudioDevice] = None):

        if device is None and self._audio_devices:
            current_index = self.main_win.combo_audio_input.currentIndex()
            if 0 <= current_index < len(self._audio_devices):
                device = self._audio_devices[current_index]

        try:
            new_recorder = AudioRecorder(device, self)
        except Exception as e:
            raise RuntimeError(f"AudioRecorder の初期化に失敗しました: {e}") from e
        
        if not new_recorder.is_valid():
            error_msg = new_recorder.get_init_error()
            QMessageBox.critical(self.main_win, "録音デバイスエラー", error_msg)
            logger.critical(f"AudioRecorder initialization failed: {error_msg}")
            new_recorder.deleteLater()
            return

        if hasattr(self, '_recorder') and self._recorder:
            self._recorder.stop()
            self._recorder.deleteLater()

        self._recorder = new_recorder
        
        self._recorder.started.connect(self._on_record_started)
        self._recorder.errorOccurred.connect(self._on_record_error)
        self._recorder.levelChanged.connect(self._on_audio_level_changed)

    def stop_hardware(self):
        if self._recorder:
            self._recorder.stop()

    def _reset_recorder_reference(self):
        if hasattr(self, '_recorder') and self._recorder:
            try:
                self._recorder.stop()
            except Exception:
                pass
            try:
                self._recorder.deleteLater()
            except Exception:
                pass
        self._recorder = None

    def toggle_recording(self):
        if not self._recorder:
            QMessageBox.critical(
                self.main_win,
                "録音エラー",
                "録音ハンドラの初期化に失敗しました。\n"
                "対応するマイクが見つからないか、サポートされていない音声形式です。"
            )
            logger.error("toggle_recording called but self._recorder is None.")
            return

        if not self._audio_devices:
            QMessageBox.warning(
                self.main_win,
                "マイクが見つかりません",
                "録音に使用できるマイクが接続されていません。\nPCにマイクを接続してから、アプリケーションを再起動してください。"
            )
            return

        if self._recorder.is_recording():
            self._stop_record()
        else:
            if self.main_win.list_pages.currentRow() >= 0:
                self._start_record()
            else:
                self.main_win.statusBar().showMessage("録音するページを選択してください", STATUS_BAR_MSG_DURATION_MS)

    def is_recording(self) -> bool:
        if self._recorder:
            return self._recorder.is_recording()
        return False

    def _start_record(self):
        if not self.session_manager:
            QMessageBox.critical(self.main_win, "エラー", "セッションマネージャーが初期化されていません。プロジェクトを新規作成または開いてください。")
            return
            
        main = self.main_win

        if self.session_manager.is_active():
            QMessageBox.warning(
                main, 
                "処理の競合", 
                "現在、別の音声処理が実行中です。完了するまでお待ちください。"
            )
            logger.warning("Recording start blocked due to an active audio session.")
            return

        row = main.list_pages.currentRow()
        if row < 0 or not main._project:
            QMessageBox.warning(main, "録音", "録音するページを選択してください。")
            return
            
        page = main._project.pages[row]
        if page.locked:
            QMessageBox.warning(
                main, "録音できません",
                "このページの音声は上書きがロックされています。\n"
                "録音し直すには、ページを右クリックしてロックを解除してください。"
            )
            return

        main.level_monitor_bar.setValue(0)
        main.level_monitor_bar.setVisible(True)
        
        main.playback_handler.stop_playback()
        main.playback_handler.recreate_player()

        target_abs_path = self.session_manager.start_session(page)
        if not target_abs_path:
            QMessageBox.critical(main, "エラー", "音声セッションの開始に失敗しました。既存の音声ファイルのバックアップに問題が発生した可能性があります。")
            return

        page.audio_source_info = "録音"

        page.audio = None
        page.duration = None
        main.waveform_widget.clear_waveform()

        main.page_list_manager.update_list_item_text(row)
        main.page_list_manager.update_total_duration()

        self._recorder.start(target_abs_path)

    def _stop_record(self):
        main = self.main_win
        if not self._recorder.is_recording() or not self.session_manager or not self.session_manager.is_active():
            return
        
        elapsed_msecs = self._record_elapsed_timer.elapsed()
        
        self._recorder.stop()
        self._reset_recording_ui()
        
        target_file = self.session_manager.get_target_abs_path()
        row = main.list_pages.currentRow()

        if elapsed_msecs < MIN_RECORDING_DURATION_SEC * 1000:
            QMessageBox.warning(
                main, "録音が短すぎます",
                f"録音時間が{int(MIN_RECORDING_DURATION_SEC)}秒未満です。音声は保存されませんでした。"
            )
            
            original_state = self.session_manager.abort_session()
            
            if main._project and 0 <= row < len(main._project.pages):
                page = main._project.pages[row]
                page.audio = original_state.get("audio")
                page.duration = original_state.get("duration")
                page.audio_source_info = original_state.get("audio_source_info")
            
            if 0 <= row < main.list_pages.count():
                self.main_win.page_list_manager.refresh()
                self.main_win.list_pages.setCurrentRow(row)

            main._set_ui_state("idle")
        
        else:
            if row >= 0 and target_file:
                main.worker_handler.start_audio_processing(target_file, row)

    def _reset_recording_ui(self):
        main = self.main_win
        self._record_timer.stop()
        main.label_record_time.setVisible(False)
        main.level_monitor_bar.setVisible(False)
        main.btn_record_stop.setText("● 録音")
        main.btn_record_stop.setStyleSheet(REC_BUTTON_STYLE)

    def _on_record_started(self):
        main = self.main_win
        main.label_record_time.setText("00:00")
        main.label_record_time.setVisible(True)
        self._record_elapsed_timer.start()
        self._record_timer.start()

        main.btn_record_stop.setText("■ 停止")
        main.btn_record_stop.setStyleSheet(STOP_BUTTON_STYLE)
        main.statusBar().showMessage("録音中...")
        main._set_ui_state("recording")

    def _on_record_error(self, msg: str):
        main = self.main_win
        was_recording = (main._current_state == "recording")

        if was_recording:
            self._reset_recording_ui()

        main.statusBar().showMessage("録音エラー", STATUS_BAR_MSG_DURATION_MS)
        QMessageBox.critical(main, "録音エラー", msg)
        
        if self.session_manager and self.session_manager.is_active():
            logger.info("An error occurred during recording. Attempting to restore from backup.")
            original_state = self.session_manager.abort_session()
            
            row = main.list_pages.currentRow()
            
            if main._project and 0 <= row < len(main._project.pages):
                page = main._project.pages[row]
                page.audio = original_state.get("audio")
                page.duration = original_state.get("duration")
                page.audio_source_info = original_state.get("audio_source_info")
                logger.info(f"Restored page metadata for row {row}.")
            
            if 0 <= row < main.list_pages.count():
                main.page_list_manager.refresh()
                main.list_pages.setCurrentRow(row)
        else:
            logger.info("Recording error occurred without an active session.")
            row = main.list_pages.currentRow()
            if main._project and 0 <= row < len(main._project.pages):
                page_id_to_reset = main._project.pages[row].page_id
                main._reset_page_audio_state_on_error(page_id_to_reset)
        
        main._set_ui_state("idle")

    def _on_audio_level_changed(self, level: float):
        main = self.main_win
        level_int = int(level * 100)
        main.level_monitor_bar.setValue(level_int)
        
        if level > 0.9:
            main.level_monitor_bar.setStyleSheet(LEVEL_BAR_STYLE_RED)
        elif level > 0.6:
            main.level_monitor_bar.setStyleSheet(LEVEL_BAR_STYLE_YELLOW)
        else:
            main.level_monitor_bar.setStyleSheet(LEVEL_BAR_STYLE_GREEN)

    def update_record_time_slot(self):
        msecs = self._record_elapsed_timer.elapsed()
        total_seconds = msecs // 1000
        
        minutes, seconds = divmod(total_seconds, 60)
        self.main_win.label_record_time.setText(f"{minutes:02d}:{seconds:02d}")