# workers.py
import os
import shutil
import json
import logging
import urllib.request
import zipfile
import hashlib
import uuid
from urllib.error import URLError
from typing import List, Tuple
from PySide6.QtCore import QObject, Signal, Slot, QUrl
from PySide6.QtNetwork import QNetworkAccessManager, QNetworkRequest, QNetworkReply
from pathlib import Path
import tempfile

from models import Project
from persistence import process_new_images, save_project_to_zip
from exporter import export_project_to_mp4
from utils import (
    resample_audio, audio_duration_seconds, trim_audio_end,
    load_waveform_data, get_media_duration_seconds, ffmpeg_executable_path,
    get_audio_stream_info, FFprobeError, get_data_storage_path
)
from config import (
    MIN_AUDIO_FILESIZE_BYTES, AUDIO_RATE, AUDIO_TRIM_END_DURATION_SEC,
    MIN_RECORDING_DURATION_SEC, MIN_AUDIO_DURATION_SEC,
    GITHUB_REPO_ID, APP_INTERNAL_NAME, APP_VERSION,
    FFMPEG_INSTALL_DIR, FFMPEG_API_URL, FFMPEG_TARGET_ZIP_FILENAME,
    FFMPEG_GLOBAL_CHECKSUM_FILENAME, FFMPEG_DOWNLOADER_USER_AGENT
)

logger = logging.getLogger(__name__)

FFMPEG_DOWNLOAD_CANCELED = "ダウンロードがキャンセルされました。"

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
    storage_path = get_data_storage_path()
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


class AudioProcessingWorker(QObject):
    finished = Signal(str, str, float, object)  # page_id, rel_path, duration, waveform_data
    error = Signal(str, str)                    # page_id, error_message

    def __init__(self, work_dir: str, audio_path: str, widget_width: int, page_id: str):
        super().__init__()
        self.work_dir = Path(work_dir)
        self.audio_path = Path(audio_path)
        self.widget_width = widget_width
        self.page_id = page_id

    def process(self):
        temp_work_path = self.audio_path.with_name(self.audio_path.stem + "_processing.wav")
        
        try:
            if not self.audio_path.exists() or self.audio_path.stat().st_size < MIN_AUDIO_FILESIZE_BYTES:
                raise RuntimeError("録音に失敗したか、音声が短すぎます。")
            
            if not resample_audio(str(self.audio_path), str(temp_work_path), int(AUDIO_RATE)):
                logger.warning("Resampling/Normalization failed, working with copy of original.")
                shutil.copy2(self.audio_path, temp_work_path)
            
            original_duration = audio_duration_seconds(str(temp_work_path))
            
            if original_duration > AUDIO_TRIM_END_DURATION_SEC:
                temp_trim_path = self.audio_path.with_name(self.audio_path.stem + "_trimming.wav")
                
                # Y2 comment:
                # Trim the very end of the audio clip. This is to remove the audible "click"
                if trim_audio_end(str(temp_work_path), str(temp_trim_path), AUDIO_TRIM_END_DURATION_SEC):
                    temp_trim_path.replace(temp_work_path)
                    logger.info("Successfully trimmed audio end.")
                else:
                    logger.warning("Trimming failed, keeping untrimmed version.")
                    if temp_trim_path.exists():
                        try:
                            temp_trim_path.unlink()
                        except OSError:
                            pass

            temp_work_path.replace(self.audio_path)
            logger.info(f"Audio processing complete for {self.audio_path.name}")

            audio_rel_path = self.audio_path.relative_to(self.work_dir)
            rel_path_posix = audio_rel_path.as_posix()
            duration = audio_duration_seconds(str(self.audio_path))
            waveform_data = load_waveform_data(str(self.audio_path), self.widget_width)
            self.finished.emit(self.page_id, rel_path_posix, duration, waveform_data)
        except Exception as e:
            self.error.emit(self.page_id, str(e))
            if self.audio_path.exists():
                try: self.audio_path.unlink()
                except OSError: pass

class AudioImportWorker(QObject):
    finished = Signal(str, str, float, object) # page_id, rel_path, duration, waveform_data
    error = Signal(str, str)                   # page_id, error_message

    def __init__(self, work_dir: str, source_path: str, target_wav_path: str, widget_width: int, page_id: str):
        super().__init__()
        self.work_dir = Path(work_dir)
        self.source_path = Path(source_path)
        self.target_wav_path = Path(target_wav_path)
        self.widget_width = widget_width
        self.page_id = page_id

    def process(self):
        temp_wav_path = self.target_wav_path.with_suffix(".wav.tmp")
        
        try:
            if not resample_audio(str(self.source_path), str(temp_wav_path), int(AUDIO_RATE)):
                codec_name = '不明'
                sample_rate = 'N/A'
                try:
                    stream_info = get_audio_stream_info(str(self.source_path))
                    codec_name = stream_info.get('codec_name', '不明')
                    sample_rate = stream_info.get('sample_rate', 'N/A')
                except FFprobeError as e:
                    logger.warning(f"Could not get audio info for error message: {e}")
                except Exception as e:
                    logger.warning(f"Unexpected error getting audio info: {e}")
                
                error_detail = (
                    f"音声ファイルの変換に失敗しました。\n\n"
                    f"ファイル情報: コーデック={codec_name}, サンプルレート={sample_rate}Hz\n\n"
                    "ファイルが破損しているか、非対応の形式である可能性があります。"
                )
                raise RuntimeError(error_detail)
            
            final_duration = get_media_duration_seconds(str(temp_wav_path))

            if final_duration < MIN_RECORDING_DURATION_SEC:
                raise RuntimeError(f"音声ファイルが短すぎます。{MIN_RECORDING_DURATION_SEC:.1f}秒以上のファイルが必要です。（検出された長さ: {final_duration:.2f}秒）")

            if final_duration <= MIN_AUDIO_DURATION_SEC:
                 raise RuntimeError(f"音声処理後に有効な音声データを取得できませんでした。（検出された長さ: {final_duration:.2f}秒）")


            if self.target_wav_path.exists():
                self.target_wav_path.unlink()
            temp_wav_path.rename(self.target_wav_path)
            
            target_rel_path = self.target_wav_path.relative_to(self.work_dir)
            rel_path_posix = target_rel_path.as_posix()
            
            waveform_data = load_waveform_data(str(self.target_wav_path), self.widget_width)
            
            self.finished.emit(self.page_id, rel_path_posix, final_duration, waveform_data)

        except Exception as e:
            self.error.emit(self.page_id, str(e))
            if temp_wav_path.exists():
                try:
                    temp_wav_path.unlink()
                except OSError:
                    pass
            if self.target_wav_path.exists() and self.target_wav_path.stat().st_size < MIN_AUDIO_FILESIZE_BYTES:
                try:
                    self.target_wav_path.unlink()
                except OSError:
                    pass

class ImageImportWorker(QObject):
    finished = Signal(list, int, list) # new_pages, initial_page_count, error_messages
    update_progress = Signal(str)
    error = Signal(str)

    def __init__(self, work_dir: str, source_paths: List[str], initial_page_count: int):
        super().__init__()
        self.work_dir = work_dir
        self.source_paths = source_paths
        self.initial_page_count = initial_page_count

    def process(self):
        try:
            new_pages, error_list = process_new_images(
                self.work_dir, self.source_paths, self.update_progress.emit
            )
            self.finished.emit(new_pages, self.initial_page_count, error_list)
        except Exception as e:
            self.error.emit(f"画像のインポート中に予期せぬエラーが発生しました: {e}")

class ExportWorker(QObject):
    finished = Signal(bool)
    error = Signal(str)
    update_progress = Signal(int, int, str)

    def __init__(self, project: Project, project_folder: str, out_path: str):
        super().__init__()
        self.project = project
        self.project_folder = project_folder
        self.out_path = out_path
        self._is_canceled = False

    def request_cancel(self):
        self._is_canceled = True

    def process(self):
        
        def progress_callback(current_step: int, total_steps: int, message: str):
            self.update_progress.emit(current_step, total_steps, message)

        try:
            export_project_to_mp4(
                self.project, self.project_folder, self.out_path,
                lambda: self._is_canceled,
                progress_callback
            )
            self.finished.emit(False)
        except InterruptedError:
            logger.info("Export process was successfully canceled by the user.")
            self.finished.emit(True)
        except Exception as e:
            if not self._is_canceled:
                self.error.emit(str(e))
            else:
                logger.info(f"Export process canceled (Exception caught: {e}).")
                self.finished.emit(False)

class SaveProjectWorker(QObject):
    finished = Signal(bool, str)
    update_progress = Signal(int, int, str)  # current, total, message

    def __init__(self, work_dir: str, project: Project, save_path: str, skip_size_check: bool = False):
        super().__init__()
        self.work_dir = work_dir
        self.project = project
        self.save_path = save_path
        self.skip_size_check = skip_size_check
        self._is_canceled = False

    def request_cancel(self):
        self._is_canceled = True

    def process(self):
        try:
            if not self.skip_size_check:
                self.update_progress.emit(0, 0, "プロジェクトサイズを確認中...")
                from utils import get_directory_uncompressed_size
                from config import MAX_UNCOMPRESSED_PROJECT_SIZE_BYTES
                uncompressed_size = get_directory_uncompressed_size(Path(self.work_dir))

                if self._is_canceled:
                    self.finished.emit(False, "SAVE_CANCELED")
                    return

                if uncompressed_size > MAX_UNCOMPRESSED_PROJECT_SIZE_BYTES:
                    size_gb = uncompressed_size / (1024**3)
                    self.finished.emit(False, f"SIZE_LIMIT:{size_gb:.2f}")
                    return

            self.update_progress.emit(0, 0, "プロジェクトを保存中...")

            save_project_to_zip(
                self.work_dir, self.project, self.save_path,
                progress_callback=self._report_progress,
                is_canceled_callback=lambda: self._is_canceled
            )

            if self._is_canceled:
                self.finished.emit(False, "SAVE_CANCELED")
            else:
                self.finished.emit(True, self.save_path)
        except InterruptedError:
            self.finished.emit(False, "SAVE_CANCELED")
        except Exception as e:
            logger.error(f"SaveProjectWorker failed: {e}", exc_info=True)
            self.finished.emit(False, str(e))

    def _report_progress(self, current: int, total: int, message: str):
        self.update_progress.emit(current, total, message)


class AssetExportWorker(QObject):
    finished = Signal(int, list)  # exported_count, error_messages
    update_progress = Signal(int, int, str)  # current, total, message

    def __init__(self, pages_data: list, work_dir: str, dest_dir: str):
        super().__init__()
        self.pages_data = pages_data
        self.work_dir = Path(work_dir)
        self.dest_dir = Path(dest_dir)

    def process(self):
        exported_count = 0
        error_messages = []
        total_items = len(self.pages_data)

        try:
            for index, page_info in enumerate(self.pages_data):
                page_number = page_info["page_number"]
                image_rel = page_info.get("image")
                audio_rel = page_info.get("audio")

                if image_rel:
                    source_path = self.work_dir / image_rel
                    if source_path.exists():
                        try:
                            ext = source_path.suffix
                            dest_path = self.dest_dir / f"ページ{page_number}{ext}"
                            shutil.copy2(source_path, dest_path)
                            exported_count += 1
                        except (IOError, OSError) as e:
                            err_msg = f"・ページ{page_number}の画像 ({source_path.name}) のコピーに失敗: {e}"
                            logger.error(err_msg)
                            error_messages.append(err_msg)

                if audio_rel:
                    source_path = self.work_dir / audio_rel
                    if source_path.exists():
                        try:
                            dest_path = self.dest_dir / f"ページ{page_number}.wav"
                            shutil.copy2(source_path, dest_path)
                            exported_count += 1
                        except (IOError, OSError) as e:
                            err_msg = f"・ページ{page_number}の音声 ({source_path.name}) のコピーに失敗: {e}"
                            logger.error(err_msg)
                            error_messages.append(err_msg)

                self.update_progress.emit(
                    index + 1, total_items,
                    f"素材をエクスポート中... ({index + 1}/{total_items})")

            self.finished.emit(exported_count, error_messages)
        except Exception as e:
            logger.error(f"AssetExportWorker failed: {e}", exc_info=True)
            error_messages.append(f"予期せぬエラー: {e}")
            self.finished.emit(exported_count, error_messages)


class UpdateChecker(QObject):
    finished = Signal(str, str)
    error = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._manager = QNetworkAccessManager(self)
        self._manager.finished.connect(self._on_reply_finished)

    @Slot()
    def process(self):
        try:
            
            api_url = f"https://api.github.com/repos/{GITHUB_REPO_ID}/releases/latest"
            request = QNetworkRequest(QUrl(api_url))
            request.setHeader(QNetworkRequest.KnownHeaders.UserAgentHeader, "DougaMeijin-Update-Checker")
            
            self._manager.get(request)
        except Exception as e:
            self.error.emit(f"アップデートチェックの開始に失敗しました: {e}")

    @Slot(QNetworkReply)
    def _on_reply_finished(self, reply: QNetworkReply):
        try:
            if reply.error() != QNetworkReply.NetworkError.NoError:
                error_string = reply.errorString()
                self.error.emit(f"ネットワークの問題によりアップデートの確認に失敗しました: {error_string}")
            else:
                response_bytes = reply.readAll().data()
                json_data = json.loads(response_bytes)
                
                latest_version = json_data.get("tag_name")
                release_url = json_data.get("html_url")

                if latest_version and release_url:
                    self.finished.emit(latest_version, release_url)
                else:
                    self.error.emit("API応答の形式が正しくありません。")

        except json.JSONDecodeError:
            self.error.emit("API応答をJSONとして解析できませんでした。")
        except Exception as e:
            self.error.emit(f"アップデート情報の解析中にエラーが発生しました: {e}")
        finally:
            reply.deleteLater()

class FFmpegCheckWorker(QObject):
    finished = Signal(str)

    def process(self):
        try:
            path = ffmpeg_executable_path()
            self.finished.emit(path)
        except FileNotFoundError:
            self.finished.emit("")

class FFmpegDownloadWorker(QObject):
    progress = Signal(int, int, str)  # value, total, message
    finished = Signal(bool, str)      # success, message

    def __init__(self):
        super().__init__()
        self._is_canceled = False

    def request_cancel(self):
        self._is_canceled = True

    def process(self):
        temp_dir = Path(tempfile.gettempdir())
        zip_path = temp_dir / "ffmpeg-download.zip"

        try:
            # Step 1: Get URLs and hash from GitHub API
            self.progress.emit(0, 100, "GitHub APIに接続中...")
            if self._is_canceled: raise InterruptedError(FFMPEG_DOWNLOAD_CANCELED)
            
            zip_url, expected_hash = _get_urls_and_hash()
            
            # Step 2: Download the ZIP file
            if self._is_canceled: raise InterruptedError(FFMPEG_DOWNLOAD_CANCELED)
            self._download_file(zip_url, zip_path)

            # Step 3: Verify the hash
            self.progress.emit(0, 100, "ダウンロードファイルを検証中...")
            if self._is_canceled: raise InterruptedError(FFMPEG_DOWNLOAD_CANCELED)
            
            if not _verify_hash(zip_path, expected_hash, lambda p, t, m: self.progress.emit(p, t, m)):
                if self._is_canceled: raise InterruptedError(FFMPEG_DOWNLOAD_CANCELED)
                raise RuntimeError("ハッシュ検証に失敗しました。ファイルが破損しているか、改ざんされた可能性があります。")
            
            # Step 4: Install the ZIP file
            self.progress.emit(0, 0, "FFmpegをインストール (展開) 中...")
            if self._is_canceled: raise InterruptedError(FFMPEG_DOWNLOAD_CANCELED)
            
            _install_zip(zip_path)
            
            self.finished.emit(True, "FFmpegのセットアップが完了しました。")

        except InterruptedError as e:
            self.finished.emit(False, str(e))
        except Exception as e:
            self.finished.emit(False, f"エラーが発生しました: {e}")
        finally:
            if zip_path.exists():
                try:
                    zip_path.unlink()
                except OSError:
                    pass
    
    def _download_file(self, url, out_path: Path):
        import urllib.request
        from config import FFMPEG_DOWNLOADER_USER_AGENT
        
        req = urllib.request.Request(url, headers={"User-Agent": FFMPEG_DOWNLOADER_USER_AGENT})
        with urllib.request.urlopen(req, timeout=20) as response:
            total_size = int(response.getheader('Content-Length', -1))

            if total_size > 0:
                temp_dir = out_path.parent
                free_space = shutil.disk_usage(temp_dir).free
                required_space = total_size + (50 * 1024 * 1024)
                if free_space < required_space:
                    raise OSError(
                        f"ダウンロードに必要なディスク空き容量が不足しています。\n"
                        f"必要な容量: 約 {total_size / 1024 / 1024:.0f} MB, "
                        f"利用可能な容量: {free_space / 1024 / 1024:.0f} MB"
                    )

            downloaded = 0
            chunk_size = 8 * 1024 * 1024 # 8MB chunk

            try:
                with open(out_path, 'wb') as f:
                    while True:
                        if self._is_canceled:
                            raise InterruptedError(FFMPEG_DOWNLOAD_CANCELED)
                        
                        chunk = response.read(chunk_size)
                        if not chunk:
                            break
                        
                        f.write(chunk)
                        downloaded += len(chunk)

                        message = f"ダウンロード中... ({downloaded / 1024 / 1024:.1f} MB"
                        if total_size > 0:
                             message += f" / {total_size / 1024 / 1024:.1f} MB)"
                        else:
                             message += ")"
                        self.progress.emit(downloaded, total_size, message)
            except OSError as e:
                raise IOError(f"ファイルの書き込みに失敗しました。ディスクの空き容量が不足している可能性があります。\n詳細: {e}")

class WaveformLoadWorker(QObject):
    finished = Signal(str, object)  # page_id, waveform_data (np.ndarray)
    error = Signal(str, str)        # page_id, error_message

    def __init__(self, audio_path: str, widget_width: int, page_id: str):
        super().__init__()
        self.audio_path = audio_path
        self.widget_width = widget_width
        self.page_id = page_id

    @Slot()
    def process(self):
        try:
            logger.info(f"WaveformLoadWorker starting for page_id: {self.page_id}")
            waveform_data = load_waveform_data(self.audio_path, self.widget_width)
            if waveform_data is None:
                raise RuntimeError("load_waveform_data returned None.")
            self.finished.emit(self.page_id, waveform_data)
        except Exception as e:
            logger.error(f"WaveformLoadWorker failed for page {self.page_id}: {e}")
            self.error.emit(self.page_id, f"波形データの読み込みに失敗しました: {e}")