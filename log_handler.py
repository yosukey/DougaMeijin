# log_handler.py
import logging
from PySide6.QtCore import QObject, Signal

class QtLogHandler(QObject, logging.Handler):
    new_record = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        logging.Handler.__init__(self)
        formatter = logging.Formatter(
            '[%(asctime)s] [%(levelname)-8s] [%(name)s:%(lineno)d] %(message)s',
            datefmt='%H:%M:%S'
        )
        self.setFormatter(formatter)

    def emit(self, record: logging.LogRecord):
        try:
            msg = self.format(record)
            self.new_record.emit(msg)
        except Exception:
            self.handleError(record)