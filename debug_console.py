# debug_console.py
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QPlainTextEdit,
    QDialogButtonBox, QFileDialog, QMessageBox
)
from PySide6.QtCore import Slot, Qt, QEvent

from utils import get_system_info_header
from datetime import datetime


class DebugConsoleDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Debug Console")
        self.setMinimumSize(700, 400)
        
        self.setWindowFlags(self.windowFlags() | Qt.WindowType.Window) 

        layout = QVBoxLayout(self)
        
        self.text_browser = QPlainTextEdit(self)
        self.text_browser.setReadOnly(True)
        layout.addWidget(self.text_browser)

        button_box = QDialogButtonBox()
        save_button = button_box.addButton("Save Log...", QDialogButtonBox.ButtonRole.ActionRole)
        clear_button = button_box.addButton("Clear", QDialogButtonBox.ButtonRole.ActionRole)
        close_button = button_box.addButton("Close", QDialogButtonBox.ButtonRole.RejectRole)
        
        save_button.clicked.connect(self._save_log_to_file)
        clear_button.clicked.connect(self.text_browser.clear)
        close_button.clicked.connect(self.reject) 
        
        layout.addWidget(button_box)

    @Slot(str)
    def append_text(self, text: str):
        self.text_browser.appendPlainText(text)

    def _save_log_to_file(self):
        
        # 1. Generate the system info header
        try:
            system_info = get_system_info_header()
        except Exception as e:
            system_info = f"--- Failed to generate system info ---\n{e}\n\n"

        # 2. Get the current time as the "Save Time"
        try:
            save_time = datetime.now().astimezone()
            save_time_str = f"Log Saved: {save_time.strftime('%Y-%m-%d %H:%M:%S %Z')}\n\n"
        except Exception:
             save_time_str = f"Log Saved (UTC): {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S UTC')}\n\n"

        # 3. Get the current log content
        log_content = self.text_browser.toPlainText()
        
        # 4. Combine them all
        full_log_content = system_info + save_time_str + log_content

        if not log_content.strip():
            if not system_info.strip():
                QMessageBox.information(self, "Save Log", "Log is empty, nothing to save.")
                return

        path, _ = QFileDialog.getSaveFileName(
            self, 
            "Save Log As...", 
            "dougameijin_debug_log.txt", 
            "Text Files (*.txt);;All Files (*)"
        )
        
        if path:
            try:
                with open(path, 'w', encoding='utf-8') as f:
                    # 5. Write the combined string to the file
                    f.write(full_log_content)
            except IOError as e:
                QMessageBox.critical(self, "Save Error", f"Failed to save log file:\n{e}")
                print(f"ERROR: Failed to save log to {path}. Reason: {e}")

    def closeEvent(self, event: QEvent):
        self.hide()
        event.ignore()