# waveform.py
import numpy as np
from PySide6.QtCore import Qt, QPointF, Signal
from PySide6.QtGui import QPainter, QPen, QPalette, QFontMetrics, QPainterPath
from PySide6.QtWidgets import QWidget
from config import WAVEFORM_TIMELINE_HEIGHT, NO_PLAYBACK_POSITION
import os

AUDIO_FILE_EXTENSIONS = {'.wav', '.mp3', '.aac', '.m4a', '.flac', '.ogg', '.opus', '.wma'}

class WaveformWidget(QWidget):
    seekRequested = Signal(float)
    audioFileDropped = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._waveform_data: np.ndarray | None = None
        self._playback_position_ratio: float = NO_PLAYBACK_POSITION
        self._duration_seconds: float = 0.0

        pal = self.palette()
        pal.setColor(QPalette.Window, Qt.black)
        self.setAutoFillBackground(True)
        self.setPalette(pal)
        
        self.setAcceptDrops(True)

    def set_waveform(self, data: np.ndarray, duration_sec: float):
        self._waveform_data = data
        self._duration_seconds = duration_sec
        self.update()

    def clear_waveform(self):
        self._waveform_data = None
        self._duration_seconds = 0.0
        self._playback_position_ratio = NO_PLAYBACK_POSITION
        self.update()

    def update_position(self, ratio: float):
        self._playback_position_ratio = ratio
        self.update()

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton and self._duration_seconds > 0:
            ratio = event.position().x() / self.width()
            ratio = max(0.0, min(1.0, ratio))
            self.seekRequested.emit(ratio)

    def dragEnterEvent(self, event):
        if not self.isEnabled():
            event.ignore()
            return
            
        if event.mimeData().hasUrls():
            urls = event.mimeData().urls()
            if len(urls) != 1:
                event.ignore()
                return
            
            local_path = urls[0].toLocalFile()
            if local_path:
                ext = os.path.splitext(local_path)[1].lower()
                if ext in AUDIO_FILE_EXTENSIONS:
                    event.acceptProposedAction()
                    return

        event.ignore()

    def dropEvent(self, event):
        if event.mimeData().hasUrls():
            urls = event.mimeData().urls()
            if len(urls) == 1:
                file_path = urls[0].toLocalFile()
                if file_path:
                    self.audioFileDropped.emit(file_path)
                    event.acceptProposedAction()
                    return
        event.ignore()

    def paintEvent(self, event):
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        w = self.width()
        h = self.height()

        if self._waveform_data is None or len(self._waveform_data) == 0:
            painter.setPen(Qt.darkGray)
            painter.drawText(self.rect(), Qt.AlignCenter, "音声がありません (録音 または 音声ファイル D&D)")
            return

        if self._duration_seconds > 0:
            wave_h = h - WAVEFORM_TIMELINE_HEIGHT
            
            if self._duration_seconds <= 10:
                interval = 1.0
            elif self._duration_seconds <= 60:
                interval = 5.0
            elif self._duration_seconds <= 180:
                interval = 10.0
            elif self._duration_seconds <= 600:
                interval = 30.0
            else:
                interval = 60.0

            grid_pen = QPen(Qt.darkGray)
            grid_pen.setStyle(Qt.DotLine)
            painter.setPen(grid_pen)

            time_steps = np.arange(interval, self._duration_seconds, interval)
            for t in time_steps:
                x = (t / self._duration_seconds) * w
                painter.drawLine(QPointF(x, 0), QPointF(x, wave_h))
                
                label = str(int(t))
                fm = QFontMetrics(painter.font())
                label_width = fm.horizontalAdvance(label)
                painter.drawText(QPointF(x - label_width / 2, h - 3), label)
        else:
            wave_h = h

        h_center = wave_h / 2.0
        wave_pen = QPen(self.palette().color(QPalette.Highlight))
        wave_pen.setWidth(1)
        painter.setPen(wave_pen)
        
        num_points = len(self._waveform_data) // 2
        if num_points == 0:
            return

        x_step = w / num_points
        
        path = QPainterPath()
        
        for i in range(num_points):
            x = i * x_step
            
            min_val = self._waveform_data[i * 2]
            max_val = self._waveform_data[i * 2 + 1]

            y_min = h_center - min_val * h_center
            y_max = h_center - max_val * h_center
            
            if abs(y_max - y_min) < 1.0:
                y_max = y_min + 1.0

            path.moveTo(QPointF(x, y_min))
            path.lineTo(QPointF(x, y_max))

        painter.drawPath(path)

        if 0.0 <= self._playback_position_ratio <= 1.0:
            pos_x = self._playback_position_ratio * w
            cursor_pen = QPen(Qt.red)
            cursor_pen.setWidth(2)
            painter.setPen(cursor_pen)
            painter.drawLine(int(pos_x), 0, int(pos_x), wave_h)