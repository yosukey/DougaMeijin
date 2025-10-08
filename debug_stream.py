# debug_stream.py
import sys
from PySide6.QtCore import QObject, Signal
from datetime import datetime

class StdStreamHandler(QObject):
    messageWritten = Signal(str)

    def __init__(self, original_stream=None, is_stderr=False, parent=None):
        super().__init__(parent)
        self._original_stream = original_stream
        self._is_stderr = is_stderr

    def write(self, text: str):
        if self._original_stream:
            self._original_stream.write(text)

        line_stripped = text.strip()
        if not line_stripped:
            return

        timestamp = datetime.now().strftime('%H:%M:%S.%f')[:-3]
        
        error_prefix = "[ERROR] " if self._is_stderr else ""
        
        formatted_message = f"[{timestamp}] {error_prefix}{text.rstrip()}"
        self.messageWritten.emit(formatted_message)

    def flush(self):
        if self._original_stream:
            self._original_stream.flush()