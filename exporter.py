# exporter.py
import os
import ctypes
import shutil
import subprocess
import tempfile
import sys
import time
import math
import logging
from pathlib import Path
from models import Project
from utils import ffmpeg_executable_path
from config import (
    RESOLUTION_MAP,
    H264_PRESET,
    H264_CRF,
    AUDIO_BITRATE,
    AUDIO_RATE,
    AUDIO_CHANNELS,
    GOP_MULTIPLIER,
    MIN_EXPORT_FPS,
    MIN_AUDIO_DURATION_SEC,
    TRANSITION_TOTAL_SECONDS
)

logger = logging.getLogger(__name__)

def _get_startup_info():
    if sys.platform == "win32":
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        return startupinfo
    return None

def _get_base_export_options(gop: int) -> list:
    return [
        # Video options
        '-c:v', 'libx264',
        '-preset', H264_PRESET,
        '-crf', H264_CRF,
        '-g', str(gop),
        '-pix_fmt', 'yuv420p',
        '-color_primaries', 'bt709',
        '-color_trc', 'bt709',
        '-colorspace', 'bt709',
        # Audio options
        '-c:a', 'aac',
        '-b:a', AUDIO_BITRATE,
        '-ac', AUDIO_CHANNELS,
        '-ar', AUDIO_RATE,
    ]

def _run_subprocess(command: list, is_canceled_callback=None):
    if sys.platform == "win32":
        ES_CONTINUOUS = 0x80000000
        ES_SYSTEM_REQUIRED = 0x00000001
        ctypes.windll.kernel32.SetThreadExecutionState(ES_CONTINUOUS | ES_SYSTEM_REQUIRED)

    try:
        startupinfo = _get_startup_info()
        
        with tempfile.TemporaryFile(mode="w+", encoding="utf-8", errors="replace") as stderr_file:
            process = subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=stderr_file, startupinfo=startupinfo)
            
            last_read_pos = 0
            while True:
                if is_canceled_callback and is_canceled_callback():
                    try:
                        process.terminate()
                        time.sleep(0.1)
                        if process.poll() is None:
                            process.kill()
                    except OSError:
                        pass
                    raise InterruptedError("Export canceled by user during subprocess execution.")

                stderr_file.seek(last_read_pos)
                new_output = stderr_file.read()
                if new_output:
                    for line in new_output.strip().split('\n'):
                        logger.debug(f"[FFmpeg] {line}")
                    last_read_pos = stderr_file.tell()

                return_code = process.poll()
                if return_code is not None:
                    stderr_file.seek(last_read_pos)
                    final_output = stderr_file.read()
                    if final_output:
                        for line in final_output.strip().split('\n'):
                            logger.debug(f"[FFmpeg] {line}")

                    if return_code == 0:
                        return
                    else:
                        stderr_file.seek(0)
                        full_stderr_output = stderr_file.read()
                        error_message = f"FFmpeg command failed with exit code {return_code}.\n"
                        error_message += f"Command: {' '.join(command)}\n"
                        error_message += f"Stderr: {full_stderr_output}"
                        raise RuntimeError(error_message)
                
                time.sleep(0.1)

    except Exception as e:
        if isinstance(e, InterruptedError):
            raise e
        
        raise RuntimeError(f"FFmpeg process failed: {e}")
    finally:
        if sys.platform == "win32":
            ES_CONTINUOUS = 0x80000000
            ctypes.windll.kernel32.SetThreadExecutionState(ES_CONTINUOUS)


def _create_extended_videos_from_images(
    ffmpeg_path: str,
    image_path1, image_path2, duration, fps, resolution, 
    base_options, temp_dir: Path, index, is_canceled_callback=None
):
    width, height = resolution
    prev_ext = temp_dir / f'prev_extended_{index}.mp4'
    next_ext = temp_dir / f'next_extended_{index}.mp4'

    channel_layout = "mono" if AUDIO_CHANNELS == "1" else "stereo"
    silent_audio_src = f'anullsrc=channel_layout={channel_layout}:sample_rate={AUDIO_RATE}'


    for image_file, output_video in [(image_path1, prev_ext), (image_path2, next_ext)]:
        if is_canceled_callback and is_canceled_callback():
            raise InterruptedError("Export canceled by user.")

        # Y2 comment: xfade logic requires these temporary, silent (anullsrc) clips.
        # This is by design. Visual-only fade; audio is handled by 'acrossfade'.
        # Duration is a fixed, frame-quantized interval (see TRANSITION_TOTAL_SECONDS).
        command = [
            ffmpeg_path, '-y',
            '-hide_banner',
            '-loglevel', 'error',
            '-loop', '1', '-i', image_file,
            '-f', 'lavfi', '-i', silent_audio_src,
            '-vf', f"scale={width}:{height}:force_original_aspect_ratio=decrease,pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,format=yuv420p",
            *base_options,
            '-t', f"{duration:.3f}", '-r', str(fps),
            '-shortest',
            str(output_video)
        ]
        _run_subprocess(command, is_canceled_callback)
    return prev_ext, next_ext

def _export_simple_concat(
    ffmpeg_path: str,
    project: Project, pages_with_audio: list, project_folder: str, 
    out_path: str, is_canceled_callback=None, progress_callback=None
):
    ffmpeg = ffmpeg_path
    width, height = RESOLUTION_MAP.get(project.resolution, (1280, 720))
    fps = max(MIN_EXPORT_FPS, project.fps)
    gop = fps * GOP_MULTIPLIER
    
    base_options = _get_base_export_options(gop)
    
    tmpdir = Path(tempfile.mkdtemp(prefix="sbv_export_"))
    
    total_steps = len(pages_with_audio) + 1
    current_step = 0
    
    try:
        seg_paths = []
        for i, page in enumerate(pages_with_audio, start=1):
            current_step += 1
            if progress_callback:
                progress_message = f"ステップ {current_step}/{total_steps}: メインクリップ {i} を処理中..."
                progress_callback(current_step, total_steps, progress_message)

            if is_canceled_callback and is_canceled_callback():
                raise InterruptedError("Export canceled by user.")

            img = Path(project_folder) / page.image
            aud = Path(project_folder) / page.audio
            duration = max(MIN_AUDIO_DURATION_SEC, page.duration)
            seg = tmpdir / f"seg_{i:03d}.mp4"
            vf = f"scale={width}:{height}:force_original_aspect_ratio=decrease,pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,format=yuv420p"
            
            num_frames = math.ceil(duration * fps)
            quantized_duration = num_frames / fps

            cmd = [
                ffmpeg, "-y",
                '-hide_banner',
                '-loglevel', 'error',
                "-loop", "1", "-i", str(img), "-i", str(aud),
                "-t", f"{quantized_duration:.3f}", "-r", str(fps), "-vf", vf,
                *base_options,
                "-shortest", str(seg),
            ]
            _run_subprocess(cmd, is_canceled_callback)
            seg_paths.append(seg)
        
        if is_canceled_callback and is_canceled_callback():
            raise InterruptedError("Export canceled by user.")

        list_path = tmpdir / "list.txt"
        with list_path.open("w", encoding="utf-8") as f:
            for p in seg_paths:
                f.write(f"file '{p.as_posix()}'\n")
        
        current_step += 1
        if progress_callback:
            progress_message = f"ステップ {current_step}/{total_steps}: 最終的な動画ファイルを作成中..."
            progress_callback(current_step, total_steps, progress_message)

        cmd2 = [
            ffmpeg, "-y",
            '-hide_banner',
            '-loglevel', 'error',
            "-f", "concat", "-safe", "0", "-i", str(list_path),
            "-c", "copy", "-movflags", "+faststart", out_path,
        ]

        if is_canceled_callback and is_canceled_callback():
            raise InterruptedError("Export canceled by user.")
        
        _run_subprocess(cmd2, is_canceled_callback)
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)

def _export_with_xfade(
    ffmpeg_path: str,
    project: Project, pages_with_audio: list, project_folder: str, 
    out_path: str, is_canceled_callback=None, progress_callback=None
):
    ffmpeg = ffmpeg_path
    width, height = RESOLUTION_MAP.get(project.resolution, (1280, 720))
    fps = max(MIN_EXPORT_FPS, project.fps)
    gop = fps * GOP_MULTIPLIER
    
    base_options = _get_base_export_options(gop)
    transition_type = project.transition
    
    transition_frames = math.ceil(TRANSITION_TOTAL_SECONDS * fps)
    quantized_transition_sec = transition_frames / fps

    tmpdir = Path(tempfile.mkdtemp(prefix="sbv_export_xfade_"))
    
    total_steps = len(pages_with_audio)
    if len(pages_with_audio) > 1:
        total_steps += (len(pages_with_audio) - 1)
    total_steps += 1
    current_step = 0

    try:
        clips_to_concat = []
        num_pages = len(pages_with_audio)


        for i, page in enumerate(pages_with_audio):
            if is_canceled_callback and is_canceled_callback():
                raise InterruptedError("Export canceled by user.")

            current_step += 1
            if progress_callback:
                progress_message = f"ステップ {current_step}/{total_steps}: メインクリップ {i+1} を処理中..."
                progress_callback(current_step, total_steps, progress_message)

            main_clip_path = tmpdir / f"main_{i:03d}.mp4"
            img_path = Path(project_folder) / page.image
            audio_path = Path(project_folder) / page.audio
            duration = max(MIN_AUDIO_DURATION_SEC, page.duration)
            
            main_num_frames = math.ceil(duration * fps)
            quantized_main_duration = main_num_frames / fps
            
            vf_main = f"scale={width}:{height}:force_original_aspect_ratio=decrease,pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,format=yuv420p"
            cmd_main = [
                ffmpeg, "-y",
                '-hide_banner',
                '-loglevel', 'error',
                "-loop", "1", "-i", str(img_path), "-i", str(audio_path),
                "-t", f"{quantized_main_duration:.3f}", "-r", str(fps), "-vf", vf_main,
                *base_options, "-shortest", str(main_clip_path)
            ]
            _run_subprocess(cmd_main, is_canceled_callback)
            clips_to_concat.append(main_clip_path)

            if i < num_pages - 1:
                if is_canceled_callback and is_canceled_callback():
                    raise InterruptedError("Export canceled by user.")

                current_step += 1
                if progress_callback:
                    progress_message = f"ステップ {current_step}/{total_steps}: トランジション {i+1} を処理中..."
                    progress_callback(current_step, total_steps, progress_message)

                transition_clip_path = tmpdir / f"transition_{i:03d}.mp4"
                next_page = pages_with_audio[i+1]
                next_img_path = Path(project_folder) / next_page.image

                prev_ext, next_ext = _create_extended_videos_from_images(
                    ffmpeg_path,
                    str(img_path), str(next_img_path), 
                    quantized_transition_sec, 
                    fps, (width, height), base_options, tmpdir, i,
                    is_canceled_callback
                )

                filter_complex = (
                    f"[0:v][1:v]xfade=transition={transition_type}:duration={quantized_transition_sec}:offset=0[v];"
                    f"[0:a][1:a]acrossfade=d={quantized_transition_sec}[a]"
                )
                
                cmd_transition = [
                    ffmpeg, "-y",
                    '-hide_banner',
                    '-loglevel', 'error',
                    "-i", str(prev_ext),
                    "-i", str(next_ext),
                    "-filter_complex", filter_complex,
                    "-map", "[v]", "-map", "[a]",
                    *base_options,
                    str(transition_clip_path)
                ]
                
                if is_canceled_callback and is_canceled_callback():
                    raise InterruptedError("Export canceled by user.")

                _run_subprocess(cmd_transition, is_canceled_callback)
                clips_to_concat.append(transition_clip_path)

                prev_ext.unlink()
                next_ext.unlink()

        if is_canceled_callback and is_canceled_callback():
            raise InterruptedError("Export canceled by user.")

        list_path = tmpdir / "list.txt"
        with list_path.open("w", encoding="utf-8") as f:
            for p in clips_to_concat:
                f.write(f"file '{p.as_posix()}'\n")

        current_step += 1
        if progress_callback:
            progress_message = f"ステップ {current_step}/{total_steps}: 最終的な動画ファイルを作成中..."
            progress_callback(current_step, total_steps, progress_message)

        cmd_final = [
            ffmpeg, "-y",
            '-hide_banner',
            '-loglevel', 'error',
            "-f", "concat", "-safe", "0", "-i", str(list_path),
            "-c", "copy", "-movflags", "+faststart", out_path,
        ]
        
        if is_canceled_callback and is_canceled_callback():
            raise InterruptedError("Export canceled by user.")

        _run_subprocess(cmd_final, is_canceled_callback)
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)

def export_project_to_mp4(
    project: Project, project_folder: str, out_path: str, 
    is_canceled_callback=None, progress_callback=None
) -> None:
    pages_with_audio = [p for p in project.pages if p.audio and p.duration and p.duration >= MIN_AUDIO_DURATION_SEC]
    if not pages_with_audio:
        raise RuntimeError("音声が録音されているページがありません。書き出しを行うには、まず音声を録音してください。")
    
    ffmpeg_path = ffmpeg_executable_path()
    
    if project.transition == "none" or len(pages_with_audio) <= 1:
        _export_simple_concat(
            ffmpeg_path,
            project, pages_with_audio, project_folder, out_path, 
            is_canceled_callback, progress_callback
        )
    else:
        _export_with_xfade(
            ffmpeg_path,
            project, pages_with_audio, project_folder, out_path, 
            is_canceled_callback, progress_callback
        )