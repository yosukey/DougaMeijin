# page_list_manager.py
from __future__ import annotations
import os
import logging
from typing import TYPE_CHECKING, List

from PySide6.QtCore import Qt, QSize, QStandardPaths
from PySide6.QtGui import QIcon, QPixmap
from PySide6.QtWidgets import (
    QListWidget, QMenu, QMessageBox, QFileDialog, QListWidgetItem
)

from models import Page
from persistence import remove_pages_from_project
from utils import natural_sort_key
from config import (
    THUMBNAIL_HEIGHT, PAGE_LIST_ITEM_PADDING, LIST_WIDGET_FONT_SIZE,
    PROJECT_FILE_EXTENSION, IMAGE_PDF_FILE_FILTER,
    MIN_AUDIO_DURATION_SEC, TRANSITIONS, TRANSITION_TOTAL_SECONDS,
    DIR_THUMBNAILS
)

if TYPE_CHECKING:
    from main_window import MainWindow

logger = logging.getLogger(__name__)

class PageListManager:
    def __init__(self, main_win: MainWindow, list_widget: QListWidget):
        self.main_win = main_win
        self.list_widget = list_widget

    def refresh(self):
        main = self.main_win
        if main._project:
            main.stacked_widget.setCurrentIndex(1)

            main.combo_resolution.blockSignals(True)
            main.combo_transition.blockSignals(True)

            main.combo_resolution.setCurrentText(main._project.resolution)
            
            display_text = TRANSITIONS.get(main._project.transition, TRANSITIONS["none"])
            main.combo_transition.setCurrentText(display_text)
            
            main.combo_resolution.blockSignals(False)
            main.combo_transition.blockSignals(False)
        else:
            main.stacked_widget.setCurrentIndex(0)

        current_row = self.list_widget.currentRow()
        
        self.list_widget.blockSignals(True)
        self.list_widget.clear()
        
        if main._project and main._work_dir:
            font = self.list_widget.font()
            font.setPointSize(LIST_WIDGET_FONT_SIZE)
            self.list_widget.setFont(font)

            for i, page in enumerate(main._project.pages):
                item = self.create_page_list_item(page, i)
                self.list_widget.addItem(item)
        
        self.list_widget.blockSignals(False)

        if main._project and main._project.pages:
            new_row = current_row if current_row != -1 and current_row < len(main._project.pages) else 0
            if self.list_widget.currentRow() != new_row:
                self.list_widget.setCurrentRow(new_row)
            else:
                main._on_select_page(new_row)
        else:
            main._on_select_page(-1)
        
        self.update_total_duration()
        main._update_window_title()

    def add_pages(self):
        main = self.main_win
        if not main._project or not main._work_dir:
            QMessageBox.warning(main, "画像を追加", "まずプロジェクトを開くか、新規作成してください。")
            return
        
        documents_path = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.DocumentsLocation)
        imgs, _ = QFileDialog.getOpenFileNames(
            main, 
            "プロジェクトに追加する画像またはPDFを選択", 
            documents_path,
            IMAGE_PDF_FILE_FILTER
        )
        if not imgs: return
        
        main.worker_handler.start_image_import(sorted(imgs, key=natural_sort_key))

    def remove_selected_pages(self):
        main = self.main_win
        selected_items = self.list_widget.selectedItems()
        if not selected_items or not main._project or not main._work_dir: return
        reply = QMessageBox.question(main, "ページの削除", f"{len(selected_items)}個のページを削除しますか？\nこの操作は元に戻せません。", QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if reply == QMessageBox.No: return
        pages_to_delete = [main._project.pages[self.list_widget.row(item)] for item in selected_items]
        indices_to_delete = sorted([self.list_widget.row(item) for item in selected_items], reverse=True)
        
        logger.info(f"Removing {len(indices_to_delete)} pages at indices: {indices_to_delete}")
        remove_pages_from_project(str(main._work_dir), main._project.pages, pages_to_delete)
        
        for idx in indices_to_delete:
            del main._project.pages[idx]
        main._mark_as_dirty()
        self.refresh()

    def on_pages_reordered(self, parent, start, end, destination, row):
        main = self.main_win
        if not main._project: return
        
        logger.info(f"Reordering pages: Moving item from index {start} to {row}")
        moved_page = main._project.pages.pop(start)

        insert_index = row
        if row > start:
            insert_index -= 1
        
        main._project.pages.insert(insert_index, moved_page)

        main._mark_as_dirty()
        
        current_selection_index = self.list_widget.currentRow()
        self.refresh()
        if current_selection_index >= 0 and current_selection_index < self.list_widget.count():
            self.list_widget.setCurrentRow(current_selection_index)

    def show_page_context_menu(self, pos):
        main = self.main_win
        item = self.list_widget.itemAt(pos)
        if not item or not main._project:
            return

        row = self.list_widget.row(item)
        page = main._project.pages[row]
        has_audio = page.audio and page.duration and page.duration > MIN_AUDIO_DURATION_SEC

        menu = QMenu()
        
        duplicate_action = menu.addAction("📑 複製")
        duplicate_action.triggered.connect(self.duplicate_current_page)
        menu.addSeparator()
        
        if has_audio:
            if page.locked:
                unlock_action = menu.addAction("🔓 音声の上書きロックを解除")
                unlock_action.triggered.connect(lambda: self.toggle_page_lock(row))
            else:
                lock_action = menu.addAction("🔒 音声の上書きをロック")
                lock_action.triggered.connect(lambda: self.toggle_page_lock(row))
        
        menu.exec(self.list_widget.mapToGlobal(pos))

    def duplicate_current_page(self):
        main = self.main_win
        if not main._project:
            return
            
        row = self.list_widget.currentRow()
        if not (0 <= row < len(main._project.pages)):
            return

        source_page = main._project.pages[row]
        logger.info(f"Duplicating page at index {row} (ID: {source_page.page_id})")
        
        new_page = Page(
            image=source_page.image,
            original_filename=source_page.original_filename,
            pdf_page_number=source_page.pdf_page_number,
            original_resolution=source_page.original_resolution,
            exif_orientation=source_page.exif_orientation,
            audio=None,
            duration=None,
            locked=False
        )

        insert_position = row + 1
        main._project.pages.insert(insert_position, new_page)

        main._mark_as_dirty()
        self.refresh()
        
        self.list_widget.setCurrentRow(insert_position)

    def toggle_page_lock(self, row):
        main = self.main_win
        if not main._project or not (0 <= row < len(main._project.pages)):
            return

        main._project.pages[row].locked = not main._project.pages[row].locked
        logger.info(f"Toggled lock for page {row}. New lock state: {main._project.pages[row].locked}")
        main._mark_as_dirty()
        
        self.refresh()
        self.list_widget.setCurrentRow(row)

    def get_page_display_text(self, page: Page, index: int) -> str:
        audio_info = ""
        if page.audio and page.duration and page.duration > MIN_AUDIO_DURATION_SEC:
            minutes, seconds = divmod(int(page.duration), 60)
            duration_str = f"{minutes:02d}:{seconds:02d}"
            audio_info = f"\n{duration_str}"
        
        return f"ページ {index + 1}{audio_info}"

    def update_list_item_text(self, row: int):
        main = self.main_win
        if not main._project or not (0 <= row < self.list_widget.count()):
            return

        item = self.list_widget.item(row)
        if not item:
            return
            
        page = main._project.pages[row]
        
        item_text = self.get_page_display_text(page, row)
        item.setText(item_text)

        tooltip_text = self.get_page_tooltip_text(page)
        item.setToolTip(tooltip_text)

        item.setData(Qt.UserRole, row)
        
        has_audio = bool(page.audio and page.duration and page.duration > MIN_AUDIO_DURATION_SEC)
        
        item.setData(Qt.UserRole + 1, page.locked)
        item.setData(Qt.UserRole + 2, has_audio)

    def update_total_duration(self):
        main = self.main_win
        if not main._project:
            main.total_duration_label.setText("合計時間: 00:00")
            return
        total_seconds = sum(p.duration for p in main._project.pages if p.duration is not None)
        
        if main._project.transition != "none":
            pages_with_audio = [p for p in main._project.pages if p.audio and p.duration and p.duration > MIN_AUDIO_DURATION_SEC]
            if len(pages_with_audio) > 1:
                num_transitions = len(pages_with_audio) - 1
                total_seconds += num_transitions * TRANSITION_TOTAL_SECONDS

        minutes, seconds = divmod(int(total_seconds), 60)
        main.total_duration_label.setText(f"合計時間 (目安): {minutes:02d}:{seconds:02d}")

    def get_page_tooltip_text(self, page: Page) -> str:
        tooltip_parts = []
        if page.original_filename:
            tooltip_parts.append(f"インポートした画像ファイル名： {page.original_filename}")
        if page.original_resolution:
            tooltip_parts.append(f"元の解像度： {page.original_resolution}")

        if page.audio and page.duration and page.duration > MIN_AUDIO_DURATION_SEC:
            if page.audio_source_info:
                tooltip_parts.append(f"音声： {page.audio_source_info}")
            else:
                tooltip_parts.append("音声： 録音済み")

        if page.pdf_page_number is not None:
            tooltip_parts.append(f"PDF ページ： {page.pdf_page_number}")
        if page.exif_orientation:
            tooltip_parts.append(f"回転情報： {page.exif_orientation}")
        if page.locked:
            tooltip_parts.append("（音声の上書きはロックされています）")
            
        return "\n".join(tooltip_parts)

    def create_page_list_item(self, page: Page, index: int) -> QListWidgetItem:
        main = self.main_win
        thumbnails_dir = main._work_dir / DIR_THUMBNAILS
        thumbnail_path = thumbnails_dir / os.path.basename(page.image)
        icon = QIcon(QPixmap(str(thumbnail_path)))
        
        item_text = self.get_page_display_text(page, index)
        item = QListWidgetItem(icon, item_text)
        
        has_audio = bool(page.audio and page.duration and page.duration > MIN_AUDIO_DURATION_SEC)
        
        item.setSizeHint(QSize(0, THUMBNAIL_HEIGHT + PAGE_LIST_ITEM_PADDING))
        
        tooltip_text = self.get_page_tooltip_text(page)
        item.setToolTip(tooltip_text)
        
        item.setData(Qt.UserRole, index) 
        item.setData(Qt.UserRole + 1, page.locked)
        item.setData(Qt.UserRole + 2, has_audio)
        
        return item