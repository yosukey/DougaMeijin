# main_window_ui.py
from PySide6.QtCore import Qt, QSize, Signal
from PySide6.QtGui import QAction, QIcon
from PySide6.QtWidgets import (
    QWidget, QListWidget, QLabel,
    QVBoxLayout, QHBoxLayout, QPushButton,
    QStackedWidget, QGroupBox, QFormLayout, QComboBox,
    QProgressBar, QMainWindow
)

from config import *
from waveform import WaveformWidget


class PageListWidget(QListWidget):
    # QListWidget's InternalMove reorder mutates the view but does not reliably
    # surface a single signal we can use to resync the data model. Emit our own
    # signal after the drop has been fully applied so the order can be rebuilt
    # from the visual item order.
    rowsReordered = Signal()

    def dropEvent(self, event):
        super().dropEvent(event)
        self.rowsReordered.emit()


STYLESHEET = """
QWidget {
    background-color: #f0f0f0;
    color: #333;
    font-family: "Meiryo", "MS PGothic", sans-serif;
}
QMainWindow {
    background-color: #e8e8e8;
}
QListWidget {
    background-color: #ffffff;
    border: 1px solid #cccccc;
    font-size: 14px;
}
QListWidget::item {
    padding: 10px;
}
QListWidget::item:selected {
    background-color: #0078d7;
    color: white;
}
QLabel {
    font-size: 16px;
    color: #333;
}
QPushButton {
    font-size: 14px;
    padding: 10px 15px;
    border-radius: 8px;
    background-color: #e1e1e1;
    border: 1px solid #cccccc;
}
QPushButton:hover {
    background-color: #e9e9e9;
    border-color: #aaaaaa;
}
QPushButton:pressed {
    background-color: #d1d1d1;
}
QPushButton:disabled {
    background-color: #f5f5f5;
    color: #bbbbbb;
    border-color: #dddddd;
}
QMenuBar {
    background-color: #f0f0f0;
}
QMenuBar::item:selected {
    background-color: #e1e1e1;
}
QMenu {
    background-color: #ffffff;
    border: 1px solid #cccccc;
}
QMenu::item:selected {
    background-color: #0078d7;
    color: white;
}
QStatusBar {
    background-color: #e8e8e8;
    color: #333;
}
"""

def create_button_style(base, hover, pressed) -> str:
    return f"""
        QPushButton {{
            background-color: {base};
            color: {COLOR_WHITE};
            font-weight: bold;
        }}
        QPushButton:hover {{ background-color: {hover}; }}
        QPushButton:pressed {{ background-color: {pressed}; }}
        QPushButton:disabled {{
            background-color: {COLOR_DISABLED_BG};
            color: {COLOR_DISABLED_TEXT};
            border-color: {COLOR_DISABLED_BORDER};
            font-weight: normal;
        }}
    """

REC_BUTTON_STYLE = create_button_style(COLOR_RED_BASE, COLOR_RED_HOVER, COLOR_RED_PRESSED)
STOP_BUTTON_STYLE = create_button_style(COLOR_BLUE_BASE, COLOR_BLUE_HOVER, COLOR_BLUE_PRESSED)
PLAY_BUTTON_STYLE = create_button_style(COLOR_GREEN_BASE, COLOR_GREEN_HOVER, COLOR_GREEN_PRESSED)

LEVEL_BAR_STYLE_GREEN = f"QProgressBar::chunk {{ background-color: {LEVEL_BAR_GREEN}; }}"
LEVEL_BAR_STYLE_YELLOW = f"QProgressBar::chunk {{ background-color: {LEVEL_BAR_YELLOW}; }}"
LEVEL_BAR_STYLE_RED = f"QProgressBar::chunk {{ background-color: {LEVEL_BAR_RED}; }}"


class DropAreaWidget(QWidget):
    filesDropped = Signal(list)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAcceptDrops(True)

    def dragEnterEvent(self, event):
        if self.isEnabled() and event.mimeData().hasUrls():
            urls = event.mimeData().urls()
            image_extensions = ['.png', '.jpg', '.jpeg', '.bmp', '.webp', '.pdf']
            if all(any(url.toLocalFile().lower().endswith(ext) for ext in image_extensions) for url in urls):
                event.acceptProposedAction()

    def dropEvent(self, event):
        urls = event.mimeData().urls()
        image_paths = [url.toLocalFile() for url in urls]
        if image_paths:
            self.filesDropped.emit(image_paths)

class UiBuilder:
    
    def setup_ui(self, main_win: QMainWindow):
        main_win.setWindowIcon(QIcon("assets/app_icon.png"))
        main_win.resize(INITIAL_WINDOW_WIDTH, INITIAL_WINDOW_HEIGHT)
        main_win.setStyleSheet(STYLESHEET)

        self._build_menu(main_win)
        
        main_win.stacked_widget = QStackedWidget()
        main_win.setCentralWidget(main_win.stacked_widget)

        main_win.welcome_widget = self._create_welcome_widget(main_win)
        main_win.stacked_widget.addWidget(main_win.welcome_widget)
        
        main_win.main_editor_widget = self._create_main_editor_widget(main_win)
        main_win.stacked_widget.addWidget(main_win.main_editor_widget)

    def _build_menu(self, main_win: QMainWindow):
        mb = main_win.menuBar()
        m_file = mb.addMenu("&ファイル")
        
        main_win.act_new = QAction("新規プロジェクト...", main_win)
        main_win.act_open = QAction("プロジェクトを開く...", main_win)
        main_win.act_save = QAction("プロジェクトを保存", main_win)
        main_win.act_export = QAction("動画を書き出す...", main_win)
        main_win.act_export_assets = QAction("素材ファイルのエクスポート...", main_win)
        main_win.act_quit = QAction("終了", main_win)
        
        m_file.addAction(main_win.act_new)
        m_file.addAction(main_win.act_open)
        m_file.addAction(main_win.act_save)
        m_file.addSeparator()
        m_file.addAction(main_win.act_export)
        m_file.addAction(main_win.act_export_assets)
        m_file.addSeparator()
        m_file.addAction(main_win.act_quit)

        m_tools = mb.addMenu("&ツール")
        
        main_win.act_rescan_devices = QAction("録音デバイスを再スキャン", main_win)
        m_tools.addAction(main_win.act_rescan_devices)
        
        m_tools.addSeparator()
        main_win.act_show_debug_console = QAction("デバッグコンソールを表示...", main_win)
        m_tools.addAction(main_win.act_show_debug_console)
        
        m_help = mb.addMenu("&ヘルプ")
        
        main_win.act_homepage = QAction("ホームページ...", main_win)
        m_help.addAction(main_win.act_homepage)
        m_help.addSeparator()

        main_win.act_github = QAction("プロジェクトページ (GitHub)...", main_win)
        main_win.act_about = QAction("謝辞 (Acknowledgements)...", main_win)
        m_help.addAction(main_win.act_github)
        m_help.addSeparator()
        m_help.addAction(main_win.act_about)

    def _create_welcome_widget(self, main_win: QMainWindow) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setAlignment(Qt.AlignCenter)
        title = QLabel(f"{APP_NAME}へようこそ")
        title.setAlignment(Qt.AlignCenter)
        title.setStyleSheet("font-size: 36px; font-weight: bold; margin-bottom: 20px;")
        subtitle = QLabel("プロジェクトを新規作成するか、既存のプロジェクトを開いてください。")
        subtitle.setAlignment(Qt.AlignCenter)
        subtitle.setStyleSheet("font-size: 18px; color: #666; margin-bottom: 40px;")
        
        main_win.btn_welcome_new = QPushButton("🆕  新しいプロジェクトを作成")
        main_win.btn_welcome_new.setMinimumSize(250, 60)
        
        main_win.btn_welcome_open = QPushButton("📂 既存のプロジェクトを開く")
        main_win.btn_welcome_open.setMinimumSize(250, 60)
        
        welcome_button_style = "font-size: 18px; font-weight: bold; padding: 15px;"
        main_win.btn_welcome_new.setStyleSheet(welcome_button_style)
        main_win.btn_welcome_open.setStyleSheet(welcome_button_style)
        
        layout.addWidget(title)
        layout.addWidget(subtitle)
        layout.addWidget(main_win.btn_welcome_new)
        layout.addWidget(main_win.btn_welcome_open)
        
        layout.addStretch(1)

        main_win.ffmpeg_path_label = QLabel()
        main_win.ffmpeg_path_label.setAlignment(Qt.AlignCenter)
        main_win.ffmpeg_path_label.setStyleSheet("font-size: 12px; color: #aaaaaa;")
        layout.addWidget(main_win.ffmpeg_path_label)

        return widget

    def _create_main_editor_widget(self, main_win: QMainWindow) -> QWidget:
        widget = QWidget()
        layout = QHBoxLayout(widget)
        layout.setContentsMargins(8, 8, 8, 8)
        
        # --- ページ一覧 ---
        main_win.list_pages = PageListWidget()
        main_win.list_pages.setViewMode(QListWidget.ListMode)
        main_win.list_pages.setFlow(QListWidget.TopToBottom)
        main_win.list_pages.setMovement(QListWidget.Static)
        main_win.list_pages.setResizeMode(QListWidget.Adjust)
        main_win.list_pages.setIconSize(QSize(THUMBNAIL_WIDTH, THUMBNAIL_HEIGHT))
        main_win.list_pages.setSelectionMode(QListWidget.SingleSelection)
        main_win.list_pages.setDragDropMode(QListWidget.InternalMove)
        main_win.list_pages.setDefaultDropAction(Qt.MoveAction)
        main_win.list_pages.setContextMenuPolicy(Qt.CustomContextMenu)

        # --- プレビュー ---
        main_win.label_image = QLabel(alignment=Qt.AlignCenter)
        main_win.label_image.setMinimumSize(PREVIEW_MIN_WIDTH, PREVIEW_MIN_HEIGHT)
        main_win.label_image.setStyleSheet("background:#dddddd; color:#333; border-radius: 8px; border: 1px solid #ccc;")
        main_win.label_image.setScaledContents(False)

        # --- 波形 ---
        main_win.waveform_widget = WaveformWidget()
        main_win.waveform_widget.setFixedHeight(WAVEFORM_WIDGET_HEIGHT)

        # --- 設定 ---
        settings_group = QGroupBox("プロジェクト設定")
        settings_layout = QFormLayout(settings_group)
        settings_layout.setContentsMargins(10, 15, 10, 10)
        settings_layout.setSpacing(10)

        main_win.combo_resolution = QComboBox()
        main_win.combo_resolution.addItems(list(RESOLUTION_MAP.keys()))

        main_win.combo_transition = QComboBox()
        main_win.combo_transition.addItems(list(TRANSITIONS.values()))

        settings_layout.addRow("書き出し解像度:", main_win.combo_resolution)
        settings_layout.addRow("切り替え効果:", main_win.combo_transition)

        # --- 録音デバイス ---
        audio_device_group_box = QGroupBox("録音デバイス")
        audio_device_layout = QHBoxLayout(audio_device_group_box)
        main_win.combo_audio_input = QComboBox()
        audio_device_layout.addWidget(main_win.combo_audio_input, 1)

        # --- 録音トグル（● 録音 / ■ 停止）---
        main_win.btn_record_stop = QPushButton("● 録音")
        main_win.btn_record_stop.setFixedWidth(100)

        # --- 保存 / 書き出し ---
        main_win.btn_save = QPushButton("💾 プロジェクト保存")
        main_win.btn_export = QPushButton("▶️ 動画作成")

        # --- 再生コントロール ---
        main_win.btn_play = QPushButton("▶ 再生")
        main_win.btn_pause = QPushButton("❚❚ 一時停止")
        main_win.btn_stop_playback = QPushButton("■ 停止")
        main_win.btn_play.setFixedWidth(150)
        main_win.btn_pause.setFixedWidth(150)
        main_win.btn_stop_playback.setFixedWidth(100)

        main_win.play_pause_stack = QStackedWidget()
        main_win.play_pause_stack.addWidget(main_win.btn_play)
        main_win.play_pause_stack.addWidget(main_win.btn_pause)
        
        sp_stop = main_win.btn_stop_playback.sizePolicy()
        sp_stop.setRetainSizeWhenHidden(True)
        main_win.btn_stop_playback.setSizePolicy(sp_stop)
        
        main_win.btn_play.setStyleSheet(PLAY_BUTTON_STYLE)
        main_win.btn_pause.setStyleSheet(PLAY_BUTTON_STYLE)
        stop_playback_style = create_button_style("#6c757d", "#5a6268", "#545b62")
        main_win.btn_stop_playback.setStyleSheet(stop_playback_style)

        main_win.btn_stop_playback.hide()

        # --- 録音タイマー表示 & レベルメータ ---
        shortcut_tooltip = "スペースキーで録音開始/停止"
        main_win.btn_record_stop.setToolTip(shortcut_tooltip)
        main_win.btn_record_stop.setStyleSheet(REC_BUTTON_STYLE)
        
        main_win.label_record_time = QLabel("00:00")
        main_win.label_record_time.setStyleSheet("font-size: 16px; color: #d32f2f; font-weight: bold;")
        main_win.label_record_time.setVisible(False)
        sp_time = main_win.label_record_time.sizePolicy()
        sp_time.setRetainSizeWhenHidden(True)
        main_win.label_record_time.setSizePolicy(sp_time)

        main_win.level_monitor_bar = QProgressBar()
        main_win.level_monitor_bar.setRange(0, 100)
        main_win.level_monitor_bar.setValue(0)
        main_win.level_monitor_bar.setTextVisible(False)
        main_win.level_monitor_bar.setFixedSize(120, 16)
        main_win.level_monitor_bar.setVisible(False)
        sp_level = main_win.level_monitor_bar.sizePolicy()
        sp_level.setRetainSizeWhenHidden(True)
        main_win.level_monitor_bar.setSizePolicy(sp_level)

        # --- 上部操作行 ---
        h_layout = QHBoxLayout()
        h_layout.addWidget(main_win.btn_record_stop)

        playback_controls_layout = QHBoxLayout()
        playback_controls_layout.setSpacing(4)
        playback_controls_layout.addWidget(main_win.play_pause_stack)
        playback_controls_layout.addWidget(main_win.btn_stop_playback)
        h_layout.addLayout(playback_controls_layout)

        h_layout.addWidget(main_win.label_record_time)
        h_layout.addWidget(main_win.level_monitor_bar)
        h_layout.addStretch(1)
        h_layout.addWidget(main_win.btn_save)
        h_layout.addWidget(main_win.btn_export)

        # --- ページ操作 ---
        main_win.btn_add_pages = QPushButton("➕ 画像を追加")
        main_win.btn_remove_pages = QPushButton("➖ 選択項目を削除")
        main_win.btn_remove_pages.setEnabled(False)

        page_actions_layout = QHBoxLayout()
        page_actions_layout.addWidget(main_win.btn_add_pages)
        page_actions_layout.addWidget(main_win.btn_remove_pages)

        # --- 左ペイン ---
        pages_group = QGroupBox("ページ一覧")
        pages_group_layout = QVBoxLayout(pages_group)
        pages_group_layout.setContentsMargins(5, 5, 5, 5)
        pages_group_layout.addWidget(main_win.list_pages)

        left_vbox = QVBoxLayout()
        left_vbox.addWidget(pages_group, 1)
        left_vbox.addLayout(page_actions_layout)

        # --- 右ペイン ---
        preview_group = QGroupBox("プレビュー")
        preview_group_layout = QVBoxLayout(preview_group)
        preview_group_layout.setContentsMargins(5, 5, 5, 5)
        preview_group_layout.addWidget(main_win.label_image)

        waveform_group = QGroupBox("音声波形")
        waveform_group_layout = QVBoxLayout(waveform_group)
        waveform_group_layout.setContentsMargins(5, 5, 5, 5)
        waveform_group_layout.addWidget(main_win.waveform_widget)

        right_vbox = QVBoxLayout()
        right_vbox.addWidget(preview_group, 1)
        right_vbox.addWidget(waveform_group)
        
        right_vbox.addWidget(audio_device_group_box)
        
        right_vbox.addWidget(settings_group)
        right_vbox.addLayout(h_layout)

        # --- 2カラム配置 ---
        
        main_win.left_widget = DropAreaWidget()
        main_win.left_widget.setLayout(left_vbox)
        right_widget = QWidget()
        right_widget.setLayout(right_vbox)

        layout.addWidget(main_win.left_widget, 4)
        layout.addWidget(right_widget, 6)
        return widget