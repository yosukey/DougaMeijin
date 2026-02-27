# utils.py
import os
import re
import sys
import wave
import subprocess
import logging
from typing import Optional, Tuple
import shutil
import numpy as np
from PySide6.QtCore import QThread, QStandardPaths
from PIL import Image
from pathlib import Path
from typing import Optional
from config import (
    AUDIO_CHANNELS, FFMPEG_INSTALL_DIR, FFMPEG_BIN_DIR,
    FFMPEG_EXE, FFPROBE_EXE, FFMPEG_AUDIO_FILTER,
    APP_NAME, APP_VERSION, DIR_WAVEFORMS
)
import json
import psutil
import platform
import importlib.metadata

logger = logging.getLogger(__name__)

_natural_key = re.compile(r"(\d+)|([^\d]+)")

class FFprobeError(Exception):
    pass

def natural_sort_key(s: str):
    parts = _natural_key.findall(os.path.basename(s))
    out = []
    for num, text in parts:
        if num:
            out.append((0, int(num), ""))
        else:
            out.append((1, 0, text.lower()))
    return out

def audio_duration_seconds(path: str) -> float:
    try:
        with wave.open(path, "rb") as w:
            frames = w.getnframes()
            rate = w.getframerate()
            if rate == 0: return 0.0
            return frames / float(rate)
    except Exception:
        return 0.0

def get_data_storage_path() -> Path:
    path_str = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppLocalDataLocation)
    path = Path(path_str)
    path.mkdir(parents=True, exist_ok=True)
    return path

def ffmpeg_executable_path() -> str:
    # 1. Check in the user's local app data directory (highest priority)
    storage_path = get_data_storage_path()
    installed_path = storage_path / FFMPEG_INSTALL_DIR / FFMPEG_BIN_DIR / FFMPEG_EXE
    if installed_path.is_file():
        return str(installed_path)

    # 2. Fallback: Check in the application's base directory (for development or old versions)
    if getattr(sys, 'frozen', False):
        base_dir = Path(sys.executable).parent
    else:
        base_dir = Path(__file__).parent
    
    legacy_path = base_dir / FFMPEG_INSTALL_DIR / FFMPEG_BIN_DIR / FFMPEG_EXE
    if legacy_path.is_file():
        return str(legacy_path)

    # 3. Fallback: Check if ffmpeg is in the system's PATH
    path_ffmpeg = shutil.which("ffmpeg")
    if path_ffmpeg:
        return str(path_ffmpeg)

    raise FileNotFoundError(
        "ffmpeg.exe が見つかりませんでした。\n\n"
        "動画の書き出し機能にはFFmpegが必要です。\n\n"
        "アプリケーションを再起動すると、FFmpegの自動ダウンロードとセットアップを案内するメッセージが表示されます。\n"
        "または、手動でFFmpegをインストールし、システムのPATH環境変数を通してください。"
    )

def _get_media_info(path: str) -> dict:
    try:
        ffmpeg_dir = Path(ffmpeg_executable_path()).parent
        ffprobe_path = ffmpeg_dir / FFPROBE_EXE
        ffprobe_cmd = str(ffprobe_path) if ffprobe_path.exists() else "ffprobe"

        command = [
            ffprobe_cmd,
            '-v', 'error',
            '-print_format', 'json',
            '-show_format',  # Get format info (duration)
            '-show_streams', # Get stream info (codec, etc.)
            '-i', path
        ]
    
        startupinfo = None
        if sys.platform == "win32":
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW

        result = subprocess.run(
            command,
            check=True,
            capture_output=True,
            encoding="utf-8",
            startupinfo=startupinfo
        )
        data = json.loads(result.stdout)
        return data
    except FileNotFoundError:
        msg = "ffprobeが見つかりません。FFmpegが正しくインストールされているか確認してください。"
        logger.error(msg)
        raise FFprobeError(msg)
    except (subprocess.CalledProcessError, json.JSONDecodeError, TypeError, ValueError, OSError) as e:
        msg = f"ffprobeの実行に失敗しました: {e}"
        logger.error(msg)
        raise FFprobeError(msg)


def get_media_duration_seconds(path: str) -> float:
    try:
        data = _get_media_info(path)
        duration_str = data.get("format", {}).get("duration")
        if duration_str:
            return float(duration_str)
        
        logger.warning(f"FFprobe output for {path} did not contain a duration string.")
        return 0.0
    except (TypeError, ValueError, AttributeError) as e:
        logger.warning(f"Failed to parse duration from ffprobe output. Error: {e}")
        return 0.0

def get_audio_stream_info(path: str) -> dict:
    data = _get_media_info(path)
    audio_streams = [s for s in data.get("streams", []) if s.get("codec_type") == "audio"]
    
    if audio_streams:
        return audio_streams[0]
    
    raise FFprobeError(f"ファイル内に音声ストリームが見つかりませんでした: {path}")

def resample_audio(input_path: str, output_path: str, target_rate: int) -> bool:
    ffmpeg = ffmpeg_executable_path()

    normalization_filter = FFMPEG_AUDIO_FILTER

    command = [
        ffmpeg,
        '-y',
        '-hide_banner',
        '-loglevel', 'error',
        '-i', input_path,
        '-ar', str(target_rate),
        '-ac', str(AUDIO_CHANNELS),
        '-af', normalization_filter,
        '-sample_fmt', 's16',
        '-f', 'wav',
        output_path
    ]
    try:
        startupinfo = None
        if sys.platform == "win32":
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        
        result = subprocess.run(
            command,
            check=True,
            capture_output=True,
            encoding="utf-8",
            startupinfo=startupinfo
        )
        if result.stderr:
            logger.warning(f"FFmpeg stderr during resampling:\n{result.stderr}")
        return True
    except subprocess.CalledProcessError as e:
        logger.error(f"FFmpeg audio processing failed. Error: {e.stderr}")
        return False
    except FileNotFoundError:
        logger.error(f"FFmpeg not found at {ffmpeg}. Audio processing failed.")
        return False

def load_waveform_data(path: str, target_points: int) -> Optional[np.ndarray]:
    if target_points <= 0:
        return None
    try:
        with wave.open(path, "rb") as w:
            n_channels = w.getnchannels()
            sample_width = w.getsampwidth()
            n_frames = w.getnframes()
            if n_frames == 0:
                return None
            frames_data = w.readframes(n_frames)

        if sample_width == 1:
            waveform = np.frombuffer(frames_data, dtype=np.uint8)
            waveform = waveform.astype(np.float32) - 128.0
            max_possible_amplitude = 128.0
        else:
            dtype_map = {2: np.int16, 4: np.int32}
            if sample_width not in dtype_map:
                return None
            
            dtype = dtype_map[sample_width]
            waveform = np.frombuffer(frames_data, dtype=dtype)
            waveform = waveform.astype(np.float32)
            max_possible_amplitude = float(np.iinfo(dtype).max)

        if n_channels > 1:
            waveform = waveform.reshape(-1, n_channels).mean(axis=1)

        if max_possible_amplitude > 0:
            waveform /= max_possible_amplitude

        if len(waveform) > target_points:
            chunk_size = len(waveform) // target_points
            num_chunks = target_points
            
            drawable_len = num_chunks * chunk_size
            waveform = waveform[:drawable_len]
            
            waveform = waveform.reshape(num_chunks, chunk_size)
            max_vals = waveform.max(axis=1)
            min_vals = waveform.min(axis=1)
            
            downsampled = np.empty(num_chunks * 2, dtype=np.float32)
            downsampled[0::2] = min_vals
            downsampled[1::2] = max_vals
            waveform = downsampled
        
        return waveform
    except Exception:
        return None

def get_waveform_cache_path(work_dir: Path, page_id: str) -> Path:
    cache_dir = work_dir / DIR_WAVEFORMS
    return cache_dir / f"{page_id}.npy"

def save_waveform_cache(work_dir: Path, page_id: str, data: np.ndarray):
    if data is None:
        return
    try:
        cache_path = get_waveform_cache_path(work_dir, page_id)
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        np.save(cache_path, data)
        logger.info(f"Waveform cache saved for page_id: {page_id}")
    except (IOError, OSError) as e:
        logger.error(f"Could not save waveform cache for page {page_id}. Reason: {e}")

def load_waveform_cache(work_dir: Path, page_id: str) -> Optional[np.ndarray]:
    cache_path = get_waveform_cache_path(work_dir, page_id)
    if cache_path.exists():
        try:
            data = np.load(cache_path, allow_pickle=False)
            logger.info(f"Waveform cache loaded for page_id: {page_id}")
            return data
        except (IOError, OSError, ValueError) as e:
            logger.error(f"Could not load waveform cache for page {page_id}. Reason: {e}")
            remove_waveform_cache(work_dir, page_id)
    return None

def remove_waveform_cache(work_dir: Path, page_id: str):
    cache_path = get_waveform_cache_path(work_dir, page_id)
    if cache_path.exists():
        try:
            cache_path.unlink()
            logger.info(f"Waveform cache removed for page_id: {page_id}")
        except OSError as e:
            logger.error(f"Could not remove waveform cache for page {page_id}. Reason: {e}")

def trim_audio_end(input_path: str, output_path: str, duration_sec: float) -> bool:
    ffmpeg = ffmpeg_executable_path()
    command = [
        ffmpeg,
        '-y',
        '-hide_banner',
        '-loglevel', 'error',
        '-i', input_path,
        '-af', f'areverse,atrim=start={duration_sec},areverse',
        output_path
    ]
    try:
        startupinfo = None
        if sys.platform == "win32":
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        
        subprocess.run(
            command,
            check=True,
            capture_output=True,
            encoding="utf-8",
            startupinfo=startupinfo
        )
        return True
    except subprocess.CalledProcessError as e:
        logger.error(f"FFmpeg trimming failed. Error: {e.stderr}")
        return False
    except FileNotFoundError:
        logger.error(f"FFmpeg not found at {ffmpeg}. Trimming failed.")
        return False

def get_image_metadata(source_path: str) -> dict:
    metadata = {
        "resolution": None,
        "exif_orientation": None
    }
    try:
        with Image.open(source_path) as img:
            # 1. Get pre-rotation resolution
            metadata["resolution"] = f"{img.width}x{img.height}"

            # 2. Get EXIF orientation description
            exif = img.getexif()
            orientation = exif.get(0x0112)
            if orientation:
                simple_descriptions = {
                    3: "180°回転",
                    6: "90°回転 (時計回り)",
                    8: "270°回転 (反時計回り)",
                }
                if orientation in simple_descriptions:
                     metadata["exif_orientation"] = f"回転情報: {simple_descriptions[orientation]}"

    except Exception as e:
        logger.warning(f"Could not read metadata from {source_path}: {e}")
    
    return metadata

def get_directory_uncompressed_size(dir_path: Path) -> int:
    total_size = 0
    try:
        for entry in os.scandir(dir_path):
            if entry.is_file(follow_symlinks=False):
                total_size += entry.stat(follow_symlinks=False).st_size
            elif entry.is_dir(follow_symlinks=False):
                total_size += get_directory_uncompressed_size(Path(entry.path))
    except (FileNotFoundError, OSError) as e:
        logger.warning(f"Could not calculate directory size for {dir_path}. Reason: {e}")
    return total_size

def get_system_info_header() -> str:
    
    # 1. Get FFmpeg Path (handle if not found)
    try:
        ffmpeg_path = ffmpeg_executable_path()
    except FileNotFoundError:
        ffmpeg_path = "NOT FOUND (Export disabled)"
    except Exception as e:
        ffmpeg_path = f"Error finding path: {e}"

    # 2. Get CPU and RAM Info using psutil (if available)
    try:
        processor_info = platform.processor() or "Unknown"
        phys_cores = psutil.cpu_count(logical=False) or 1
        log_cores = psutil.cpu_count(logical=True) or 1
        cpu_info = f"{processor_info} ({phys_cores} Physical Cores, {log_cores} Logical)"
    except Exception as e:
        cpu_info = f"Error getting CPU info: {e}"
    
    try:
        mem_info = psutil.virtual_memory()
        total_ram_gb = mem_info.total / (1024 ** 3)
        ram_info = f"{total_ram_gb:.2f} GB Total"
    except Exception as e:
        ram_info = f"Error getting RAM info: {e}"

    # 3. Assemble the header list
    header_lines = [
        f"--- System Information ---",
        f"App Version: {APP_NAME} v{APP_VERSION}",
        f"OS: {platform.platform()}",
        f"CPU: {cpu_info}",
        f"Total RAM: {ram_info}",
        f"Python Version: {sys.version.split()[0]}",
        f"Python Executable: {sys.executable}",
        f"FFmpeg Path: {ffmpeg_path}",
        f"",
        f"--- Key Module Versions ---"
    ]
    
    # 4. Define key packages (add psutil to the list)
    key_modules = ["PySide6", "numpy", "Pillow", "pypdfium2", "psutil"]
    
    if getattr(sys, 'frozen', False):
        header_lines.append("  (Frozen executable detected. Versions are not available at runtime.)")
    else:
        for mod_name in key_modules:
            try:
                version = importlib.metadata.version(mod_name)
                header_lines.append(f"{mod_name}: {version}")
            except importlib.metadata.PackageNotFoundError:
                header_lines.append(f"{mod_name}: NOT FOUND")
            except Exception as e:
                 header_lines.append(f"{mod_name}: Error retrieving version ({e})")
    
    header_lines.append("-----------------------------\n\n")
    return "\n".join(header_lines)

def compare_versions(v1: str, v2: str) -> int:
    try:
        parts1 = [int(p) for p in v1.strip('v').split('.')]
        parts2 = [int(p) for p in v2.strip('v').split('.')]

        len1, len2 = len(parts1), len(parts2)
        if len1 > len2:
            parts2.extend([0] * (len1 - len2))
        elif len2 > len1:
            parts1.extend([0] * (len2 - len1))

        if parts1 > parts2: return 1
        if parts1 < parts2: return -1
        return 0
    except (ValueError, TypeError):
        return 0

def gracefully_shutdown_thread(
    thread: Optional[QThread], 
    name: str, 
    timeout_ms: int = 5000, 
    force_terminate: bool = True
):
    if not thread:
        return
    
    try:
        if thread.isRunning():
            logger.info(f"Waiting for {name} thread to finish...")
            thread.quit()
            if not thread.wait(timeout_ms):
                logger.warning(f"{name} thread did not finish within {timeout_ms}ms.")
                if force_terminate:
                    logger.warning(f"Forcefully terminating {name} thread.")
                    thread.terminate()
                else:
                    logger.info(f"Termination was skipped for {name} thread as per configuration.")
    except RuntimeError:
        logger.info(f"Thread '{name}' was already deleted, no shutdown action needed.")

def prune_stale_caches(work_dir: Path, project: 'Project'):
    if not work_dir or not project:
        return

    logger.info("Starting stale cache pruning...")
    live_page_ids = {p.page_id for p in project.pages}
    pruned_count = 0

    waveform_cache_dir = work_dir / DIR_WAVEFORMS
    if waveform_cache_dir.is_dir():
        for cache_file in waveform_cache_dir.glob("*.npy"):
            page_id_from_filename = cache_file.stem
            
            if page_id_from_filename not in live_page_ids:
                try:
                    cache_file.unlink()
                    logger.info(f"  - Pruned stale waveform cache: {cache_file.name}")
                    pruned_count += 1
                except OSError as e:
                    logger.error(f"  - Could not prune cache file {cache_file.name}: {e}")
    
    if pruned_count > 0:
        logger.info(f"Cache pruning complete. Removed {pruned_count} stale file(s).")
    else:
        logger.info("Cache pruning complete. No stale files found.")
