# persistence.py
import json
import os
import shutil
import time
import zipfile
from typing import List
from PIL import Image, ImageOps, ImageFile
import io
from pathlib import Path

from models import Project, Page
from utils import audio_duration_seconds, get_image_metadata, remove_waveform_cache
from config import (
    THUMBNAIL_WIDTH, THUMBNAIL_HEIGHT, RESOLUTION_MAP,
    MAX_IMAGE_FILE_SIZE_MB, MAX_PDF_FILE_SIZE_MB, MAX_PDF_PAGE_COUNT,
    MAX_IMAGE_PIXELS,
    COLLISION_RETRY_LIMIT, MASTER_IMAGE_FORMAT_NAME, MASTER_IMAGE_EXTENSION,
    PDF_RENDER_DPI, DIR_IMAGES, DIR_THUMBNAILS, PROJECT_FILENAME,
    MAX_UNCOMPRESSED_PROJECT_SIZE_BYTES, DIR_WAVEFORMS
)
import fitz
from config import DEFAULT_RESOLUTION, DEFAULT_FPS

ImageFile.LOAD_TRUNCATED_IMAGES = True

def _ensure_dir(path: Path):
    path.mkdir(parents=True, exist_ok=True)

def _handle_collision(dst_path: Path) -> Path:
    if not dst_path.exists():
        return dst_path
    
    directory = dst_path.parent
    name = dst_path.stem
    ext = dst_path.suffix

    for i in range(1, COLLISION_RETRY_LIMIT + 1):
        new_name = f"{name} ({i}){ext}"
        new_path = directory / new_name
        if not new_path.exists():
            return new_path

    raise IOError(
        f"Could not find a unique filename for '{dst_path.name}' after "
        f"{COLLISION_RETRY_LIMIT} attempts in the directory '{directory}'."
    )

def _generate_letterboxed_image(source_img: Image.Image, target_size: tuple) -> Image.Image:
    resample_filter = Image.Resampling.LANCZOS
    
    letterboxed_img = Image.new("RGB", target_size, (0, 0, 0))

    source_width, source_height = source_img.size
    target_width, target_height = target_size

    source_ratio = source_width / source_height
    target_ratio = target_width / target_height

    if source_ratio > target_ratio:
        new_width = target_width
        new_height = int(new_width / source_ratio)
    else:
        new_height = target_height
        new_width = int(new_height * source_ratio)

    resized_img = source_img.resize((new_width, new_height), resample_filter)

    x = (target_width - new_width) // 2
    y = (target_height - new_height) // 2
    
    letterboxed_img.paste(resized_img, (x, y))
    return letterboxed_img

def _create_and_save_assets(
    source_img: Image.Image,
    base_filename: str,
    images_dir: Path,
    thumbnails_dir: Path
) -> dict:
    master_size = RESOLUTION_MAP.get("1080p")
    thumb_size = (THUMBNAIL_WIDTH, THUMBNAIL_HEIGHT)

    safe_basename = Path(base_filename).stem + MASTER_IMAGE_EXTENSION
    image_path = _handle_collision(images_dir / safe_basename)
    thumbnail_path = _handle_collision(thumbnails_dir / safe_basename)

    master_image = _generate_letterboxed_image(source_img, master_size)
    master_image.save(image_path, MASTER_IMAGE_FORMAT_NAME)

    thumbnail_image = source_img.copy()
    thumbnail_image.thumbnail(thumb_size, Image.Resampling.LANCZOS)
    
    thumb_canvas = Image.new("RGB", thumb_size, (0,0,0))
    x = (thumb_size[0] - thumbnail_image.width) // 2
    y = (thumb_size[1] - thumbnail_image.height) // 2
    thumb_canvas.paste(thumbnail_image, (x, y))
    thumb_canvas.save(thumbnail_path, MASTER_IMAGE_FORMAT_NAME)


    return {
        "image_path": image_path,
        "thumbnail_path": thumbnail_path,
    }

def process_new_images(work_dir: str, source_paths: List[str], progress_callback=None) -> tuple[List[Page], List[str]]:
    work_dir_path = Path(work_dir)
    images_dir = work_dir_path / DIR_IMAGES
    thumbnails_dir = work_dir_path / DIR_THUMBNAILS
    _ensure_dir(images_dir)
    _ensure_dir(thumbnails_dir)
    
    new_pages = []
    error_messages = []
    total_files = len(source_paths)
    
    for i, src_path in enumerate(source_paths):
        original_filename = Path(src_path).name
        ext = Path(src_path).suffix.lower()

        progress_prefix = f"[{i + 1}/{total_files}]"
        if progress_callback:
            progress_callback(f"{progress_prefix} 処理中: {original_filename}")

        try:
            file_size_mb = Path(src_path).stat().st_size / (1024 * 1024)
            if ext == ".pdf" and file_size_mb > MAX_PDF_FILE_SIZE_MB:
                message = f"・{original_filename}: PDFファイルが大きすぎるためスキップされました ({file_size_mb:.1f}MB > {MAX_PDF_FILE_SIZE_MB}MB)。"
                error_messages.append(message)
                continue
            elif ext != ".pdf" and file_size_mb > MAX_IMAGE_FILE_SIZE_MB:
                message = f"・{original_filename}: 画像ファイルが大きすぎるためスキップされました ({file_size_mb:.1f}MB > {MAX_IMAGE_FILE_SIZE_MB}MB)。"
                error_messages.append(message)
                continue

            if ext == ".pdf":
                if progress_callback:
                    progress_callback(f"{progress_prefix} PDF展開中: {original_filename}")
                
                doc = None
                try:
                    doc = fitz.open(src_path)
                    num_pdf_pages = len(doc)
                    
                    if num_pdf_pages > MAX_PDF_PAGE_COUNT:
                        message = f"・{original_filename}: PDFのページ数が多すぎるためスキップされました ({num_pdf_pages} > {MAX_PDF_PAGE_COUNT})。"
                        error_messages.append(message)
                        continue

                    for page_num in range(num_pdf_pages):
                        if progress_callback:
                            progress_callback(f"{progress_prefix} PDFページ処理中: {original_filename} ({page_num + 1}/{num_pdf_pages})")
                        
                        page = doc.load_page(page_num)
                        
                        pix = page.get_pixmap(dpi=PDF_RENDER_DPI)
                        
                        if pix.width * pix.height > MAX_IMAGE_PIXELS:
                            px_mp = pix.width * pix.height / 1_000_000
                            max_px_mp = MAX_IMAGE_PIXELS / 1_000_000
                            message = (
                                f"・{original_filename} (ページ {page_num + 1}): "
                                f"解像度が高すぎるためスキップされました ({px_mp:.1f}Mpx > {max_px_mp:.1f}Mpx)。"
                            )
                            error_messages.append(message)
                            continue

                        with Image.open(io.BytesIO(pix.tobytes("png"))) as img_from_pdf:
                            source_img = img_from_pdf.convert("RGB")

                        base_filename = f"{Path(original_filename).stem}_page_{page_num + 1:03d}{MASTER_IMAGE_EXTENSION}"
                        assets = _create_and_save_assets(source_img, base_filename, images_dir, thumbnails_dir)
                        
                        image_rel_path = assets["image_path"].relative_to(work_dir_path)
                        new_page = Page(
                            image=image_rel_path.as_posix(),
                            original_filename=original_filename,
                            pdf_page_number=page_num + 1,
                            original_resolution=f"{pix.width}x{pix.height}"
                        )
                        new_pages.append(new_page)
                finally:
                    if doc:
                        doc.close()
            
            elif ext in (".png", ".jpg", ".jpeg", ".bmp", ".webp"):
                if progress_callback:
                    progress_callback(f"{progress_prefix} 画像処理中: {original_filename}")
                
                metadata = get_image_metadata(src_path)
                
                with Image.open(src_path) as img:
                    source_img = ImageOps.exif_transpose(img).convert("RGB")
                
                assets = _create_and_save_assets(source_img, original_filename, images_dir, thumbnails_dir)

                image_rel_path = assets["image_path"].relative_to(work_dir_path)
                new_page = Page(
                    image=image_rel_path.as_posix(),
                    original_filename=original_filename,
                    original_resolution=metadata.get("resolution"),
                    exif_orientation=metadata.get("exif_orientation")
                )
                new_pages.append(new_page)

        except Image.DecompressionBombError:
            max_px_mp = MAX_IMAGE_PIXELS / 1_000_000
            error_msg = f"・{original_filename}: 画像の解像度が高すぎるためスキップされました (上限: {max_px_mp:.0f}メガピクセル)。"
            error_messages.append(error_msg)
            print(f"DecompressionBombError processing {original_filename}.")
            continue
        except MemoryError:
            error_msg = f"・{original_filename}: メモリ不足のため処理に失敗しました。ファイルが大きすぎるか、複雑すぎる可能性があります。"
            error_messages.append(error_msg)
            print(f"MemoryError processing {original_filename}.")
            if progress_callback:
                progress_callback(f"エラー: {error_msg}")
            continue
        except fitz.errors.FitzError as e:
            error_msg = f"・{original_filename}: PDFファイルの処理に失敗しました。ファイルが破損しているか、非対応の形式である可能性があります。"
            error_messages.append(error_msg)
            print(f"Fitz (PyMuPDF) error processing {original_filename}: {e}")
            if progress_callback:
                progress_callback(f"エラー: {error_msg}")
            continue
        except Exception as e:
            error_msg = f"・{original_filename}: 予期せぬエラーのため処理に失敗しました: {e}"
            error_messages.append(error_msg)
            print(error_msg)
            continue
            
    return new_pages, error_messages

def save_project_to_zip(work_dir: str, project: Project, zip_path: str):
    temp_zip_path = zip_path + ".tmp"
    work_dir_path = Path(work_dir)

    pages_data = []
    for p in project.pages:
        page_dict = {
            "image": p.image,
            "page_id": p.page_id,
            "original_filename": p.original_filename,
            "pdf_page_number": p.pdf_page_number,
            "original_resolution": p.original_resolution,
            "exif_orientation": p.exif_orientation,
            "audio": p.audio,
            "duration": p.duration,
            "locked": p.locked,
            "audio_source_info": p.audio_source_info,
        }
        pages_data.append(page_dict)

    d = {
        "version": project.version,
        "resolution": project.resolution,
        "fps": project.fps,
        "transition": project.transition,
        "pages": pages_data,
    }
    project_json_path = work_dir_path / PROJECT_FILENAME
    with project_json_path.open("w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=2)
    
    try:
        excluded_dirs = {DIR_WAVEFORMS}

        with zipfile.ZipFile(temp_zip_path, 'w', zipfile.ZIP_DEFLATED) as zf:
            for root, dirs, files in os.walk(work_dir_path):
                dirs[:] = [d for d in dirs if d not in excluded_dirs]
                
                root_path = Path(root)
                for file in files:
                    file_path = root_path / file
                    archive_name = file_path.relative_to(work_dir_path)
                    zf.write(file_path, archive_name)

        os.replace(temp_zip_path, zip_path)

    except Exception as e:
        if Path(temp_zip_path).exists():
            try:
                Path(temp_zip_path).unlink()
            except FileNotFoundError:
                pass
        raise e

def load_project_from_zip(zip_path: str, work_dir: str, skip_size_check: bool = False) -> Project:
    work_dir_path = Path(work_dir)
    if work_dir_path.exists():
        attempts = 5
        for i in range(attempts):
            try:
                shutil.rmtree(work_dir_path)
                break 
            except OSError as e:
                print(f"WARN: Attempt {i+1}/{attempts} to remove old work directory failed: {e}")
                if i < attempts - 1:
                    time.sleep(0.2)
                else:
                    raise IOError(
                        "以前のプロジェクトの一時フォルダのクリーンアップに失敗しました。\n\n"
                        f"フォルダ '{work_dir_path}' を削除できませんでした。\n"
                        "ウイルス対策ソフトや他のプログラムがフォルダにアクセスしている可能性があります。\n\n"
                        "不要なプログラムを終了するか、PCを再起動してから再度お試しください。"
                    ) from e

    _ensure_dir(work_dir_path)

    with zipfile.ZipFile(zip_path, 'r') as zf:
        if not skip_size_check:
            total_uncompressed_size = sum(info.file_size for info in zf.infolist())
            
            if total_uncompressed_size > MAX_UNCOMPRESSED_PROJECT_SIZE_BYTES:
                size_gb = total_uncompressed_size / (1024**3)
                limit_gb = MAX_UNCOMPRESSED_PROJECT_SIZE_BYTES / (1024**3)
                raise IOError(
                    f"プロジェクトの展開後サイズが大きすぎます ({size_gb:.2f} GB)。"
                    f"セキュリティ上の理由から、{limit_gb:.0f} GBを超えるプロジェクトは読み込めません。"
                )

        resolved_work_dir = work_dir_path.resolve()
        for member in zf.infolist():
            target_path = (work_dir_path / member.filename).resolve()
            
            if resolved_work_dir not in target_path.parents and target_path != resolved_work_dir:
                print(f"Security Warning: Skipping malicious path in project ZIP: {member.filename}")
                continue

            if member.is_dir():
                target_path.mkdir(parents=True, exist_ok=True)
            else:
                target_path.parent.mkdir(parents=True, exist_ok=True)
                with zf.open(member) as source, target_path.open("wb") as target:
                    shutil.copyfileobj(source, target)

    project_json_path = work_dir_path / PROJECT_FILENAME
    with project_json_path.open("r", encoding="utf-8") as f:
        d = json.load(f)
    
    pages = [Page(**p) for p in d.get("pages", [])]
    for page in pages:
        if page.audio and page.duration is None:
            try:
                audio_path = work_dir_path / page.audio
                if audio_path.exists():
                    page.duration = audio_duration_seconds(str(audio_path))
            except Exception:
                page.duration = 0.0

    proj = Project(
        version=d.get("version", 1),
        resolution=d.get("resolution", DEFAULT_RESOLUTION),
        fps=d.get("fps", DEFAULT_FPS),
        transition=d.get("transition", "none"),
        pages=pages,
    )
    proj.ensure_bounds()
    return proj

def remove_pages_from_project(work_dir: str, all_pages: List[Page], pages_to_delete: List[Page]):
    work_dir_path = Path(work_dir)
    thumbnails_dir = work_dir_path / DIR_THUMBNAILS

    deleted_uuids = {p.page_id for p in pages_to_delete}

    remaining_image_paths = set()
    for p in all_pages:
        if p.page_id not in deleted_uuids and p.image:
            remaining_image_paths.add(p.image)

    for page in pages_to_delete:
        
        remove_waveform_cache(work_dir_path, page.page_id)

        if page.audio:
            try:
                audio_path = work_dir_path / page.audio
                if audio_path.exists():
                    audio_path.unlink()
            except OSError:
                pass
        if page.image and page.image not in remaining_image_paths:
            try:
                image_abs_path = work_dir_path / page.image
                if image_abs_path.exists():
                    image_abs_path.unlink()
                
                thumbnail_path = thumbnails_dir / Path(page.image).name
                if thumbnail_path.exists():
                    thumbnail_path.unlink()
            except OSError:
                pass